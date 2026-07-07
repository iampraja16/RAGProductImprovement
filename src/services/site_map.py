"""Site code ↔ full name mapping for EMR branch_site resolution."""

_SITE_MAP_RAW = [
    ("JBY","Jembayan"),("SMD","Samarinda"),("BIN","Binungan"),
    ("TJR","Tanjung Redeb"),("MLW","Muara Lawa"),("BGL","Bengalon"),
    ("SKL","Sangkulirang"),("MDN","Medan"),("TJE","Tanjung Enim"),
    ("PLU","Palu"),("BKJ","Batukajang"),("JBI","Jambi"),
    ("MTW","Muara Teweh"),("TBG","Tabang"),("MDO","Manado"),
    ("TJG","Tanjung"),("BJM","Banjarmasin"),("SMG","Semarang"),
    ("SPT","Sampit"),("UPG","Ujungpandang"),("JKT","Jakarta"),
    ("BEK","Barinto"),("BLP","Balikpapan"),("BIU","Batukajang"),
    ("LRH","Loreh"),("BNT","Bontang"),("PLB","Palembang"),
    ("SDU","Sungai Danau"),("BDI","Bendili"),("SBY","Surabaya"),
    ("RTU","Rantau"),("MTB","Muara Tiga Besar"),("PDG","Padang"),
    ("SPR","Separi"),("LJN","Loajanan"),("TRK","Tarakan"),
    ("JYP","Jayapura"),("BTL","Batulicin"),("PKB","Pekanbaru"),
    ("BLG","Bandar Lampung"),("PTK","Pontianak"),("SGT","Sangatta"),("SGT","Sangata"),
    ("SRG","Sorong"),("BHT","Buhut"),("FRP","Freeport"),
    ("DMI","Damai"),("SBT","Sambarata"),("THP","Tuhup"),
    ("SRK","Sorowako"),("LTI","Lati"),("SGA","Sanga-sanga"),
    ("STI","Satui"),("MRW","Maruwi"),("MBO","Meulaboh"),
    ("SBW","Sumbawa"),
]

SITE_MAP = {full.lower(): code for code, full in _SITE_MAP_RAW}
SITE_MAP_REVERSE = {code: full for code, full in _SITE_MAP_RAW}

def resolve_site_mentions(query: str):
    """Replace site full names with codes in query, return (modified_query, hint).

    If no site mention found, returns (query, None).
    If found, returns query with site name replaced by code + SQL hint string.
    """
    query_lower = query.lower()
    found = []
    for full_name, code in sorted(SITE_MAP.items(), key=lambda x: -len(x[0])):
        if full_name in query_lower:
            found.append((full_name, code))

    if not found:
        return query, None

    modified = query
    hints = []
    for full_name, code in found:
        modified = modified.replace(full_name.title(), code)
        hints.append(f"branch_site = '{code}'")

    hint_str = " OR ".join(hints)
    return modified, hint_str
