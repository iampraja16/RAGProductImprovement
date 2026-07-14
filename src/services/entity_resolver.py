import logging
import json
import re
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)

MAX_MENTIONS = 5
MAX_EXPANDED_SYNONYMS = 30

EXTRACT_PROMPT = """Extract up to {max} entity mentions from this EMR maintenance query.
Entity types:
- "symptom": specific, concrete symptoms or failure modes (e.g., "oli bocor", "overheating", "hydraulic leak", "low power", "suara abnormal").
  DO NOT extract generic phrases describing symptoms like "problem yang sering terjadi", "gejala", "kondisi", "kerusakan".
- "model": machine model names (e.g., "PC200", "HD785", "D155A").
- "component": component/subsystem names (e.g., "FINAL DRIVE", "engine", "transmission", "damper").
- "part": specific part names (e.g., "seal", "injector", "floating seal", "bearing").
- "root_cause": specific root causes (e.g., "contamination", "wear", "improper adjustment", "mis machining", "loose bolt").
  DO NOT extract generic terms like "penyebab kerusakan", "faktor penyebab", "root cause".

CRITICAL INSTRUCTIONS:
1. ONLY extract concrete, specific domain terms. If the user asks generally about "problems", "root causes", or "damages" without specifying which one, DO NOT extract those generic words.
2. Ignore geographic locations (e.g., Jembayan, Samarinda, Lati, Tarakan) and customer names.
3. Ignore generic/filler words like "fault", "error", "problem", "issue", "case", "total", "count", "list", "show", "find", "data", "info", "semua", "per", "setiap", "kerusakan", "masalah", "komponen", "kendala".

Query: {query}

Return ONLY a valid JSON array, no other text:
[[{{"mention": "...", "type": "symptom|model|component|part|root_cause"}}]]
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
    "dua", "tiga", "empat", "lima", "paling", "banyak", "beserta",
    "urutan", "ranking", "jumlahnya", "hidup"
})


@dataclass
class ResolvedEntity:
    mention: str
    canonical_name: str
    entity_type: str
    neo4j_label: str
    score: float


class EntityResolver:
    def __init__(self, graph_client, llm, embedder):
        self.graph_client = graph_client
        self.llm = llm
        self.embedder = embedder

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
        if entity_type == "model":
            mention_lower = mention.lower()
            for brand, brand_code in self._BRAND_MAP.items():
                if brand in mention_lower:
                    return ResolvedEntity(
                        mention=mention,
                        canonical_name=brand_code,
                        entity_type=entity_type,
                        neo4j_label="Brand",
                        score=1.0,
                    )
                    
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
            # If no model found, return the mention itself as a raw filter, do NOT fallback to symptoms
            return ResolvedEntity(
                mention=mention,
                canonical_name=mention,
                entity_type=entity_type,
                neo4j_label="UnknownModel",
                score=0.5,
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
            if e.entity_type == "model":
                continue
            col = type_map.get(e.entity_type, e.entity_type)
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

    def resolve_community_ids(self, canonical_name: str) -> List[str]:
        query = """
        MATCH (n {name: $name})-[:IN_COMMUNITY]->(c:Community {level: 0})
        RETURN DISTINCT c.communityId AS community_id
        """
        try:
            results = self.graph_client.run_query(query, {"name": canonical_name})
            return [r["community_id"] for r in results]
        except Exception as e:
            logger.warning(f"Community ID resolution failed for '{canonical_name}': {e}")
            return []

    def _expand_synonyms(self, community_ids: List[str]) -> List[str]:
        """Get ALL entity names sharing any of the given communities (synonym expansion)."""
        if not community_ids:
            return []
        query = """
        MATCH (n)-[:IN_COMMUNITY]->(c:Community {level: 0})
        WHERE c.communityId IN $cids
        RETURN DISTINCT n.name AS name
        LIMIT $limit
        """
        try:
            results = self.graph_client.run_query(query, {"cids": community_ids, "limit": MAX_EXPANDED_SYNONYMS})
            return [r["name"] for r in results if r.get("name")]
        except Exception as e:
            logger.warning(f"Synonym expansion failed: {e}")
            return []

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
                cids = self.resolve_community_ids(entity.canonical_name)
                for cid in cids:
                    community_ids.add(cid)
                    if entity.entity_type in ("symptom", "root_cause", "component", "part"):
                        symptom_community_ids.add(cid)

        expanded_names = self._expand_synonyms(list(symptom_community_ids))

        return {
            "canonical_names": sorted(canonical_names),
            "expanded_names": sorted(expanded_names),
            "community_ids": sorted(community_ids),
            "symptom_community_ids": sorted(symptom_community_ids),
            "entities": [
                {"mention": e.mention, "canonical_name": e.canonical_name,
                 "type": e.entity_type, "score": round(e.score, 3)}
                for e in resolved
            ],
        }

    def search_emr_records(self, query: str, display_limit: int = 5, site_hint: str = None, account_hint: str = None, resolved_ctx: Optional[dict] = None) -> Dict[str, Any]:
        query = query.strip()
        if not query:
            return {"query": query, "emr_records": [], "total_count": 0, "entities": []}

        # Use pre-resolved context if available (avoids redundant LLM calls)
        if resolved_ctx:
            entities = resolved_ctx.get("entities", [])
            canonical_names = resolved_ctx.get("canonical_names", [])
            expanded_names = resolved_ctx.get("expanded_names", [])
            site_hint = site_hint or resolved_ctx.get("site_hint")
            account_hint = account_hint or resolved_ctx.get("account_hint")

            all_entity_names = list(set(canonical_names) | set(expanded_names))
            model_names = [e.get("canonical_name") for e in entities if e.get("entity_type") == "model"]

            if all_entity_names or model_names:
                total_count, emr_rows = self._find_connected_emrs(
                    all_entity_names, display_limit, model_names=model_names, site_hint=site_hint, account_hint=account_hint
                )
                if emr_rows:
                    return {
                        "query": query,
                        "emr_records": emr_rows,
                        "total_count": total_count,
                        "canonical_names": all_entity_names,
                        "entities": entities,
                    }

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
                # Combine rigid exact mentions with expanded community synonyms for best practice search
                community_info = self.resolve_mentions_to_community_ids(query)
                for en in community_info.get("expanded_names", []):
                    all_entity_names.add(en)
                    
                name_list = list(all_entity_names)
                model_names = [e.canonical_name for e in resolved if e.entity_type == "model"]
                total_count, emr_rows = self._find_connected_emrs(
                    name_list, display_limit, model_names=model_names, site_hint=site_hint, account_hint=account_hint
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

        if not mention_keywords:
            words = {w for w in query.lower().split() if len(w) > 2 and w not in _STOP_WORDS}
            emr_id_keywords = {w for w in words if re.search(r'^[a-z0-9]+[-_][a-z0-9]+$', w)}
            mention_keywords = emr_id_keywords if emr_id_keywords else words
        if not mention_keywords:
            return {"query": query, "emr_records": [], "total_count": 0, "canonical_names": [], "entities": []}
        total_count, emr_rows = self._search_emrs_by_model(mention_keywords, display_limit, site_hint=site_hint, account_hint=account_hint)
        return {
            "query": query,
            "emr_records": emr_rows,
            "total_count": total_count,
            "canonical_names": [],
            "entities": [],
        }

    def _find_connected_emrs(self, names: List[str], display_limit: int,
                             model_names: Optional[List[str]] = None,
                             site_hint: str = None, account_hint: str = None) -> Tuple[int, List[Dict]]:
        if not names and not model_names:
            return 0, []
        model_names = model_names or []
        has_models = bool(model_names)

        model_clauses = []
        model_params = {}
        brand_codes = list(self._BRAND_MAP.values())
        for i, mn in enumerate(model_names):
            pk = f"model_{i}"
            model_params[pk] = mn
            if mn in brand_codes:
                model_clauses.append(f"e.machine_product = ${pk}")
            else:
                model_clauses.append(
                    f"(toLower(e.machine_model) CONTAINS toLower(${pk}) OR toLower(e.model_family) CONTAINS toLower(${pk}))"
                )
        model_cond = " OR ".join(model_clauses) if model_clauses else "true"
        
        extra_conds = []
        if site_hint:
            extra_conds.append(f"({site_hint.replace('branch_site', 'e.branch_site')})")
        if account_hint:
            extra_conds.append(f"({account_hint.replace('account_account_name', 'e.account_account_name')})")
        extra_cond_str = f" AND {' AND '.join(extra_conds)} " if extra_conds else ""

        count_query = f"""
        MATCH (e:EMRRecord)
        OPTIONAL MATCH (e)-[:MENTIONS]->(n) WHERE n.name IN $names
        WITH e, collect(DISTINCT n.name) AS matched_patterns
        WITH e, matched_patterns,
             size([p IN matched_patterns WHERE p IS NOT NULL]) AS mention_count
        WHERE mention_count > 0
          AND ($has_models = false OR ({model_cond})){extra_cond_str}
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
        WHERE mention_count > 0
          AND ($has_models = false OR ({model_cond})){extra_cond_str}
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
            # We no longer use expected_count exact matching, mention_count > 0 is sufficient 
            # since we expanded names via synonyms, not all names will be present in every single record.
            params = {"names": names, "has_models": has_models, **model_params}
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

    def _resolve_model_communities(self, model_names: list[str]) -> list[str]:
        """Fallback: find community_ids associated with a model via EMRRecord lookup."""
        if not model_names:
            return []
        or_conds = []
        params = {}
        for i, mn in enumerate(model_names):
            pk = f"m{i}"
            params[pk] = mn
            or_conds.append(f"(toLower(e.machine_model) CONTAINS toLower(${pk}) OR toLower(e.model_family) CONTAINS toLower(${pk}))")
        cond = " OR ".join(or_conds)
        query = f"""
        MATCH (e:EMRRecord)
        WHERE {cond}
        RETURN e.community_id AS cid, count(*) AS cnt
        ORDER BY cnt DESC
        LIMIT 5
        """
        try:
            results = self.graph_client.run_query(query, params)
            cids = set()
            for r in results:
                raw = r.get("cid")
                if isinstance(raw, list):
                    for c in raw:
                        if c:
                            cids.add(c)
                elif raw:
                    cids.add(raw)
            return sorted(cids)
        except Exception as e:
            logger.warning(f"Model community fallback failed: {e}")
            return []

    def resolve_full_context(self, query: str) -> dict:
        from src.services.site_map import resolve_site_mentions
        from src.services.account_map import resolve_account_mentions

        # Resolve site names to codes BEFORE building the Vanna prompt
        site_resolved, site_hint = resolve_site_mentions(query)
        _, account_hint = resolve_account_mentions(query)

        mentions = self._extract_mentions(query)

        resolved_entities = []
        community_ids: set = set()
        symptom_community_ids: set = set()
        canonical_names: set = set()

        for m in mentions:
            entity = self._resolve_single(m["mention"], m["type"])
            if entity is not None:
                resolved_entities.append(entity)
                canonical_names.add(entity.canonical_name)
                cids = self.resolve_community_ids(entity.canonical_name)
                for cid in cids:
                    community_ids.add(cid)
                    if entity.entity_type in ("symptom", "root_cause", "component", "part"):
                        symptom_community_ids.add(cid)

        expanded_names = self._expand_synonyms(list(symptom_community_ids))
        model_entities = [e for e in resolved_entities if e.entity_type == "model"]
        has_model_entities = bool(model_entities)
        s_cids = sorted(symptom_community_ids)

        # Fallback: if no symptom entities extracted but model exists, try model-based community lookup
        # NOTE: model community fallback does NOT consider site/account constraints,
        # so we do NOT set should_inject_community here. If we injected community IDs
        # from all sites while a site filter (JBY/TRK) is also applied, the two filters
        # could contradict each other (community IDs from other sites don't exist at JBY/TRK).
        if not s_cids and model_entities:
            model_cids = self._resolve_model_communities([e.canonical_name for e in model_entities])
            if model_cids:
                s_cids = model_cids
                logger.info(f"Model community fallback (not injected): {model_cids}")

        # Inject community_id whenever community IDs are available from entity resolution.
        # This includes both symptom-based queries and model-based queries (via model community fallback).
        # Community IDs provide semantic problem clustering, enabling precise filtering even for model-level queries.
        should_inject_community = bool(s_cids)
        cid_hint = (
            " OR ".join(f"'{c}' = ANY(community_id)" for c in s_cids)
            if should_inject_community else None
        )
        # Build modified query on site-resolved text so Vanna sees site codes, not full names
        modified = self._build_modified_query(site_resolved, resolved_entities)

        serialized_entities = [
            {
                "mention": e.mention,
                "canonical_name": e.canonical_name,
                "entity_type": e.entity_type,
                "neo4j_label": e.neo4j_label,
                "score": round(e.score, 3),
            }
            for e in resolved_entities
        ]

        return {
            "entities": serialized_entities,
            "modified_query": modified,
            "community_ids": sorted(community_ids),
            "symptom_community_ids": s_cids,
            "canonical_names": sorted(canonical_names),
            "expanded_names": sorted(expanded_names),
            "site_hint": site_hint,
            "account_hint": account_hint,
            "model_entities": [e.canonical_name for e in model_entities],
            "has_model_entities": has_model_entities,
            "should_inject_community": should_inject_community,
            "cid_hint": cid_hint,
        }

    def _search_emrs_by_model(self, keywords: set, display_limit: int, site_hint: str = None, account_hint: str = None) -> Tuple[int, List[Dict]]:
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
        
        extra_conds = []
        if site_hint:
            extra_conds.append(f"({site_hint.replace('branch_site', 'e.branch_site')})")
        if account_hint:
            extra_conds.append(f"({account_hint.replace('account_account_name', 'e.account_account_name')})")
        extra_cond_str = f" AND {' AND '.join(extra_conds)} " if extra_conds else ""

        count_query = f"""
        MATCH (e:EMRRecord)
        WHERE {where_clause}{extra_cond_str}
        RETURN count(e) AS total
        """
        data_query = f"""
        MATCH (e:EMRRecord)
        WHERE {where_clause}{extra_cond_str}
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
