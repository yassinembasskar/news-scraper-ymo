"""
Multilingual keyword configuration — the single source of truth for what
this system searches for and what it filters out.

This module holds plain data only. The logic that uses it (building GNews
queries, matching titles/descriptions against these lists) lives in
`services/`, not here — keeping this file a pure config source makes it the
one place to edit when keywords need to change, without touching any logic.

IMPORTANT: proper nouns are transliterated for Arabic, not translated — an
Arabic article about Renault will say "رينو" in its title, not "Renault",
so searching Arabic text for the Latin spelling would mostly return nothing.
"""

KEYWORDS_BY_LANGUAGE = {
    "en": [
        "Yazaki",
        "Renault",
        "Stellantis",
        "Peugeot",
        "Citroen",
        "Dacia",
        "Sumitomo Electric",
        "Leoni",
        "Aptiv",
        "Lear Corporation",
        "wiring harness",
        "Tanger Med",
        "Tanger port",
        "Tangier Automotive City",
        "China port",
        "Shanghai port",
        "Suez Canal",
        "Red Sea shipping",
        "port congestion",
        "supply chain",
        "logistics",
        "world transportation",
        "automotive industry",
        "electric vehicle",
        "semiconductor shortage",
    ],
    "fr": [
        "Yazaki",
        "Renault",
        "Stellantis",
        "Peugeot",
        "Citroën",
        "Dacia",
        "Sumitomo Electric",
        "Leoni",
        "Aptiv",
        "câblage automobile",
        "faisceau de câbles",
        "Tanger Med",
        "port de Tanger",
        "Tanger Automotive City",
        "port chinois",
        "canal de Suez",
        "mer Rouge",
        "congestion portuaire",
        "chaîne d'approvisionnement",
        "logistique",
        "transport mondial",
        "industrie automobile",
        "véhicule électrique",
        "pénurie de semi-conducteurs",
    ],
    "ar": [
        "يازاكي",               # Yazaki
        "رينو",                 # Renault
        "ستيلانتيس",            # Stellantis
        "بيجو",                 # Peugeot
        "داسيا",                # Dacia
        "ميناء طنجة المتوسط",    # Tanger Med
        "ميناء طنجة",           # Tanger port
        "طنجة أوتوموتيف سيتي",   # Tangier Automotive City
        "ميناء صيني",           # China port
        "قناة السويس",          # Suez Canal
        "البحر الأحمر",         # Red Sea
        "ازدحام الموانئ",        # port congestion
        "سلسلة التوريد",        # supply chain
        "الخدمات اللوجستية",    # logistics
        "النقل العالمي",        # world transportation
        "صناعة السيارات",       # automotive industry
        "السيارات الكهربائية",  # electric vehicle
        "نقص أشباه الموصلات",   # semiconductor shortage
    ],
}

# Titles/descriptions containing any of these words (case-insensitive) get
# dropped entirely. Keyed by language, since an English exclude word can't
# filter a French or Arabic title.
EXCLUDE_WORDS_BY_LANGUAGE = {
    "en": ["job", "hiring", "recruitment"],
    "fr": ["emploi", "recrutement", "offre d'emploi"],
    "ar": ["وظيفة", "توظيف"],
}