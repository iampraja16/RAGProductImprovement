import logging
import json
import re
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MAX_MENTIONS = 5

EXTRACT_PROMPT = """Extract up to {max} entity mentions from this EMR maintenance query.
Entity types:
- "symptom": problem descriptions, symptoms, failure modes (e.g., "oli bocor", "overheating", "hydraulic leak")
- "model": machine model names (e.g., "PC200", "HD785", "D155A")
- "component": component/subsystem names (e.g., "FINAL DRIVE", "engine", "transmission")
- "part": specific part names (e.g., "seal", "injector", "floating seal")
- "root_cause": root cause descriptions (e.g., "contamination", "wear", "improper adjustment")

IMPORTANT: Ignore generic/filler words like "fault", "error", "problem", "issue", "case",
"total", "count", "list", "show", "find", "data", "info", "semua", "per", "setiap" —
these are NOT meaningful symptoms.

Query: {query}

Return ONLY a valid JSON array, no other text:
[{{"mention": "...", "type": "symptom|model|component|part|root_cause"}}]
"""

_STOP_WORDS = frozenset({
    "fault", "error", "problem", "issue", "case", "cases",
    "total", "count", "list", "show", "find", "data", "info",
    "informasi", "semua", "per", "setiap", "berapa", "cari",
    "tentang", "mengenai", "bahas", "membahas", "apakah", "adakah",
    "emr", "emrrecord", "record", "please", "tolong", "mohon",
    "saya", "kamu", "kami", "saja", "juga", "akan", "dapat",
    "yang", "dan", "atau", "di", "ke", "pada", "dengan", "untuk",
    "ini", "itu", "ada", "tidak", "the", "a", "an", "of", "in",
    "on", "to", "for", "with", "and", "or", "is", "are", "was",
    "sebutkan", "tampilkan", "berikan", "nomor", "angka", "satu",
    "dua", "tiga", "empat", "lima",
})


@dataclass
class ResolvedEntity:
    mention: str
    canonical_name: str
    entity_type: str
    neo4j_label: str
    score: float


@dataclass
class ResolvedQuery:
    original_query: str
    modified_query: str
    entities: List[ResolvedEntity] = field(default_factory=list)


class EntityResolver:
    def __init__(self, graph_client, llm, embedder):
        self.graph_client = graph_client
        self.llm = llm
        self.embedder = embedder

    def resolve_query(self, query: str) -> ResolvedQuery:
        query = query.strip()
        if not query:
            return ResolvedQuery(original_query=query, modified_query=query)

        mentions = self._extract_mentions(query)
        if not mentions:
            return ResolvedQuery(original_query=query, modified_query=query)

        resolved = []
        for m in mentions:
            entity = self._resolve_single(m["mention"], m["type"])
            if entity is not None:
                resolved.append(entity)

        modified = self._build_modified_query(query, resolved)
        return ResolvedQuery(
            original_query=query,
            modified_query=modified,
            entities=resolved,
        )

    def _extract_mentions(self, query: str) -> List[Dict]:
        from langchain_core.messages import HumanMessage
        prompt = EXTRACT_PROMPT.format(query=query, max=MAX_MENTIONS)
        try:
            response = self.llm.invoke([HumanMessage(content=prompt)])
            text = response.content.strip()
            if text.startswith("```"):
                text = text.strip("`").strip()
                if text.startswith("json"):
                    text = text[4:].strip()
            mentions = json.loads(text)
            if not isinstance(mentions, list):
                return []
            valid_types = {"symptom", "model", "component", "part", "root_cause"}
            return [
                m for m in mentions
                if isinstance(m, dict) and "mention" in m and m.get("type") in valid_types
                and m["mention"].lower().strip() not in _STOP_WORDS
            ]
        except Exception as e:
            logger.warning(f"Entity mention extraction failed: {e}")
            return []

    def _resolve_single(self, mention: str, entity_type: str) -> Optional[ResolvedEntity]:
        # For model types, prefer MachineModel match over symptom match
        if entity_type == "model":
            model_candidates = self._search_machine_models(mention)
            if model_candidates:
                best = model_candidates[0]
                return ResolvedEntity(
                    mention=mention,
                    canonical_name=best["name"],
                    entity_type=entity_type,
                    neo4j_label=best["label"],
                    score=best["score"],
                )

        candidates = self._search_all(mention)
        if not candidates:
            return None
        best = candidates[0]
        return ResolvedEntity(
            mention=mention,
            canonical_name=best["name"],
            entity_type=entity_type,
            neo4j_label=best["label"],
            score=best["score"],
        )

    @staticmethod
    def _normalize_scores(rows: List[Dict]) -> List[Dict]:
        if not rows:
            return []
        scores = [r["score"] for r in rows]
        lo, hi = min(scores), max(scores)
        if hi == lo:
            return [{**r, "score": 1.0} for r in rows]
        return [{**r, "score": (r["score"] - lo) / (hi - lo)} for r in rows]

    def _search_all(self, text: str) -> List[Dict]:
        vector = self.embedder.embed_query(text)
        all_results = []
        all_results.extend(self._normalize_scores(self._search_vector("symptom-embeddings", vector, 10)))
        all_results.extend(self._normalize_scores(self._search_vector("rootcause-embeddings", vector, 10)))
        all_results.extend(self._normalize_scores(self._search_vector("action-embeddings", vector, 10)))
        all_results.extend(self._normalize_scores(self._search_vector("cluster-embeddings", vector, 10)))
        all_results.extend(self._normalize_scores(self._search_fulltext(text)))
        all_results.extend(self._normalize_scores(self._search_machine_models(text)))
        all_results.extend(self._normalize_scores(self._search_components(text)))
        merged = {}
        for row in all_results:
            if row["id"] not in merged or row["score"] > merged[row["id"]]["score"]:
                merged[row["id"]] = row
        results = sorted(merged.values(), key=lambda x: -x["score"])
        return results[:5]

    def _search_vector(self, index_name: str, vector: List[float], k: int) -> List[Dict]:
        query = """
        CALL db.index.vector.queryNodes($index, $k, $vector)
        YIELD node, score
        RETURN elementId(node) AS id, node.name AS name, labels(node)[0] AS label, score
        """
        try:
            return self.graph_client.run_query(query, {"index": index_name, "k": k, "vector": vector})
        except Exception as e:
            logger.warning(f"Vector search on {index_name} failed: {e}")
            return []

    def _search_fulltext(self, text: str) -> List[Dict]:
        escaped = re.sub(r"[^\w\s]", " ", text).strip()
        if not escaped:
            return []
        query = """
        CALL db.index.fulltext.queryNodes('entity-names', $query)
        YIELD node, score
        RETURN elementId(node) AS id, node.name AS name, labels(node)[0] AS label, score
        """
        try:
            return self.graph_client.run_query(query, {"query": escaped})
        except Exception as e:
            logger.warning(f"Fulltext search failed: {e}")
            return []

    def _search_machine_models(self, text: str) -> List[Dict]:
        query = """
        MATCH (m:MachineModel)
        WHERE toLower(m.name) CONTAINS toLower($query)
        RETURN elementId(m) AS id, m.name AS name, 'MachineModel' AS label,
               CASE
                 WHEN toLower(m.name) = toLower($query) THEN 1.0
                 WHEN toLower(m.name) STARTS WITH toLower($query) THEN 0.8
                 ELSE 0.5
               END AS score
        ORDER BY score DESC
        LIMIT 3
        """
        try:
            return self.graph_client.run_query(query, {"query": text})
        except Exception as e:
            logger.warning(f"MachineModel search failed: {e}")
            return []

    def _search_components(self, text: str) -> List[Dict]:
        query = """
        MATCH (c:Component)
        WHERE toLower(c.name) CONTAINS toLower($query)
        RETURN elementId(c) AS id, c.name AS name, 'Component' AS label,
               CASE
                 WHEN toLower(c.name) = toLower($query) THEN 1.0
                 WHEN toLower(c.name) STARTS WITH toLower($query) THEN 0.8
                 ELSE 0.5
               END AS score
        ORDER BY score DESC
        LIMIT 3
        """
        try:
            return self.graph_client.run_query(query, {"query": text})
        except Exception as e:
            logger.warning(f"Component search failed: {e}")
            return []

    _BRAND_MAP = {
        "komatsu": "KOMAT",
        "scania": "SCNIA",
        "tadano": "TDANH",
        "nissan": "NSSAN",
        "bomag": "BOMAG",
    }

    def _build_modified_query(self, original: str, entities: List[ResolvedEntity]) -> str:
        if not entities:
            return original
        hints = []
        type_map = {
            "symptom": "symptom",
            "root_cause": "caused_of_problem",
            "component": "techcare_component",
            "part": "part_description",
            "model": "model_family",
        }
        for e in entities:
            col = type_map.get(e.entity_type, e.entity_type)
            if e.entity_type == "model":
                mention = e.mention.lower()
                brand_code = self._BRAND_MAP.get(mention)
                if brand_code:
                    hints.append(f"machine_product = '{brand_code}'")
                else:
                    hints.append(f"machine_model ILIKE '{e.mention}%'")
            else:
                hints.append(f"{col} mengandung '{e.canonical_name}'")
        if hints:
            hint_str = "; ".join(hints)
            result = original
            for e in entities:
                if e.entity_type == "model":
                    continue
                pattern = re.compile(re.escape(e.mention), re.IGNORECASE)
                result = pattern.sub(e.canonical_name, result)
            if result == original:
                result = f"{original}. Petunjuk: {hint_str}"
            return result
        return original

    def resolve_community_id(self, canonical_name: str) -> Optional[str]:
        query = """
        MATCH (n {name: $name})-[:IN_COMMUNITY]->(c:Community {level: 0})
        RETURN c.communityId AS community_id
        LIMIT 1
        """
        try:
            results = self.graph_client.run_query(query, {"name": canonical_name})
            return results[0]["community_id"] if results else None
        except Exception as e:
            logger.warning(f"Community ID resolution failed for '{canonical_name}': {e}")
            return None

    def resolve_mentions_to_community_ids(self, query: str) -> Dict[str, Any]:
        mentions = self._extract_mentions(query)
        if not mentions:
            return {"canonical_names": [], "community_ids": [], "symptom_community_ids": [], "entities": []}

        resolved = []
        community_ids = set()
        symptom_community_ids = set()
        canonical_names = set()
        for m in mentions:
            entity = self._resolve_single(m["mention"], m["type"])
            if entity is not None:
                resolved.append(entity)
                canonical_names.add(entity.canonical_name)
                cid = self.resolve_community_id(entity.canonical_name)
                if cid:
                    community_ids.add(cid)
                    if entity.entity_type in ("symptom", "root_cause", "component", "part"):
                        symptom_community_ids.add(cid)

        return {
            "canonical_names": sorted(canonical_names),
            "community_ids": sorted(community_ids),
            "symptom_community_ids": sorted(symptom_community_ids),
            "entities": [
                {"mention": e.mention, "canonical_name": e.canonical_name,
                 "type": e.entity_type, "score": round(e.score, 3)}
                for e in resolved
            ],
        }

    def search_emr_records(self, query: str, display_limit: int = 5) -> Dict[str, Any]:
        query = query.strip()
        if not query:
            return {"query": query, "emr_records": [], "total_count": 0, "entities": []}

        mentions = self._extract_mentions(query)
        mention_keywords = set()
        resolved = []
        all_entity_names = set()

        if mentions:
            for m in mentions:
                entity = self._resolve_single(m["mention"], m["type"])
                mention_keywords.add(m["mention"].lower())
                if entity is not None:
                    resolved.append(entity)
                    all_entity_names.add(entity.canonical_name)

            if all_entity_names:
                name_list = list(all_entity_names)
                model_names = [e.canonical_name for e in resolved if e.entity_type == "model"]
                total_count, emr_rows = self._find_connected_emrs(
                    name_list, display_limit, model_names=model_names
                )
                if emr_rows:
                    return {
                        "query": query,
                        "emr_records": emr_rows,
                        "total_count": total_count,
                        "canonical_names": name_list,
                        "entities": [
                            {"mention": e.mention, "canonical_name": e.canonical_name,
                             "type": e.entity_type, "score": round(e.score, 3)}
                            for e in resolved
                        ],
                    }

        # Fallback: search by raw keywords from query
        if not mention_keywords:
            words = {w for w in query.lower().split() if len(w) > 2 and w not in _STOP_WORDS}
            # If any keyword looks like an EMR ID (e.g. U-00006083), keep only that
            emr_id_keywords = {w for w in words if re.search(r'^[a-z0-9]+[-_][a-z0-9]+$', w)}
            mention_keywords = emr_id_keywords if emr_id_keywords else words
        if not mention_keywords:
            return {"query": query, "emr_records": [], "total_count": 0, "canonical_names": [], "entities": []}
        total_count, emr_rows = self._search_emrs_by_model(mention_keywords, display_limit)
        return {
            "query": query,
            "emr_records": emr_rows,
            "total_count": total_count,
            "canonical_names": [],
            "entities": [],
        }

    def _find_connected_emrs(self, names: List[str], display_limit: int,
                             model_names: Optional[List[str]] = None) -> Tuple[int, List[Dict]]:
        if not names and not model_names:
            return 0, []
        model_names = model_names or []
        has_models = bool(model_names)

        # Build model property filters
        model_clauses = []
        model_params = {}
        for i, mn in enumerate(model_names):
            pk = f"model_{i}"
            model_params[pk] = mn
            model_clauses.append(
                f"(toLower(e.machine_model) CONTAINS toLower(${pk}) OR toLower(e.model_family) CONTAINS toLower(${pk}))"
            )
        model_cond = " OR ".join(model_clauses) if model_clauses else "true"

        count_query = f"""
        MATCH (e:EMRRecord)
        OPTIONAL MATCH (e)-[:MENTIONS]->(n) WHERE n.name IN $names
        WITH e, collect(DISTINCT n.name) AS matched_patterns
        WITH e, matched_patterns,
             size([p IN matched_patterns WHERE p IS NOT NULL]) AS mention_count
        WHERE mention_count = $expected_count
          AND ($has_models = false OR ({model_cond}))
        RETURN count(DISTINCT e) AS total
        """
        data_query = f"""
        MATCH (e:EMRRecord)
        OPTIONAL MATCH (e)-[:MENTIONS]->(n) WHERE n.name IN $names
        OPTIONAL MATCH (n)-[:CAUSED_BY]->(rc:RootCausePattern)
        OPTIONAL MATCH (n)-[:RESOLVED_BY]->(act:ActionPattern)
        WITH e, collect(DISTINCT n.name) AS matched_patterns,
             collect(DISTINCT rc.name) AS root_causes,
             collect(DISTINCT act.name) AS actions
        WITH e, matched_patterns, root_causes, actions,
             size([p IN matched_patterns WHERE p IS NOT NULL]) AS mention_count
        WHERE mention_count = $expected_count
          AND ($has_models = false OR ({model_cond}))
        RETURN e.emr_name AS emr_name,
               e.symptom AS symptom,
               e.model_family AS model_family,
               e.machine_model AS machine_model,
               e.created_date AS created_date,
               matched_patterns AS matched_patterns,
               root_causes AS root_causes,
               actions AS actions
        ORDER BY e.emr_name
        LIMIT $limit
        """
        try:
            params = {"names": names, "expected_count": len(names), "has_models": has_models, **model_params}
            total_row = self.graph_client.run_query(count_query, params)
            total = total_row[0]["total"] if total_row else 0
            if total == 0:
                return 0, []
            params["limit"] = display_limit
            rows = self.graph_client.run_query(data_query, params)
            return total, rows
        except Exception as e:
            logger.warning(f"EMR record lookup failed: {e}")
            return 0, []

    def _search_emrs_by_model(self, keywords: set, display_limit: int) -> Tuple[int, List[Dict]]:
        if not keywords:
            return 0, []
        keyword_list = sorted(k for k in keywords if k)
        SEARCH_FIELDS = (
            "e.emr_name", "e.machine_model", "e.model_family", "e.symptom",
            "e.branch_site", "e.account_account_name", "e.serial_number",
            "e.status", "e.sub_call_type", "e.pmact_type", "e.subjects",
            "e.caused_of_problem", "e.action_how_was_problem_corrected",
            "e.part_description",
            "e.main_cause_part_no",
            "e.techcare_component", "e.techcare_sub_component",
            "e.machine_product",
        )
        # Use AND between keywords, OR between fields per keyword
        # (record must match ALL keywords, each in at least one field)
        and_clauses = []
        params = {}
        for i, kw in enumerate(keyword_list):
            param_key = f"kw{i}"
            params[param_key] = kw
            or_clauses = [
                f"toLower({field}) CONTAINS toLower(${param_key})"
                for field in SEARCH_FIELDS
            ]
            and_clauses.append(f"({' OR '.join(or_clauses)})")
        where_clause = " AND ".join(and_clauses)

        count_query = f"""
        MATCH (e:EMRRecord)
        WHERE {where_clause}
        RETURN count(e) AS total
        """
        data_query = f"""
        MATCH (e:EMRRecord)
        WHERE {where_clause}
        OPTIONAL MATCH (e)-[:MENTIONS]->(p)
        OPTIONAL MATCH (p)-[:CAUSED_BY]->(rc:RootCausePattern)
        OPTIONAL MATCH (p)-[:RESOLVED_BY]->(act:ActionPattern)
        WITH e, collect(DISTINCT rc.name) AS root_causes,
             collect(DISTINCT act.name) AS actions
        RETURN e.emr_name AS emr_name,
               e.symptom AS symptom,
               e.model_family AS model_family,
               e.machine_model AS machine_model,
               e.created_date AS created_date,
               [] AS matched_patterns,
               root_causes AS root_causes,
               actions AS actions
        ORDER BY e.emr_name
        LIMIT $limit
        """
        try:
            total_row = self.graph_client.run_query(count_query, params)
            total = total_row[0]["total"] if total_row else 0
            if total == 0:
                return 0, []
            params["limit"] = display_limit
            rows = self.graph_client.run_query(data_query, params)
            return total, rows
        except Exception as e:
            logger.warning(f"EMR model fallback lookup failed: {e}")
            return 0, []
