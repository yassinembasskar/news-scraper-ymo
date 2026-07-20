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

IMPORTANT: keep entries short (1-3 words). The API matches on title/description
containing the keyword, so a long exact phrase ("world transportation") rarely
appears verbatim in a real headline and mostly wastes a query slot. Prefer the
shortest phrase that stays unambiguous.

NOTE ON COVERAGE: this list does not attempt to cover every branch of the
relevance tree (e.g. air conditioning, solar, recycling). Broad/generic
categories like those would be far too noisy as standalone search terms in a
general news API. They're expected to surface incidentally, via "Yazaki" or
"automotive industry" style anchor terms, then get filtered for relevance
downstream by the embedding-based tree — not searched for directly here.
"""

KEYWORDS_BY_LANGUAGE = {
    "en": [
        # Yazaki itself
        "Yazaki",

        # Direct competitors (automotive wiring)
        "Fujikura",
        "Sumitomo Electric",
        "Leoni",
        "Aptiv",
        "Lear Corporation",

        # Raw material & equipment suppliers
        "Coficab",
        "Nexans",
        "Komax",
        "Schleuniger",
        "DB Schenker",

        # OEM clients
        "Renault",
        "Dacia",
        "Stellantis",
        "Peugeot",
        "Citroen",
        "Opel",
        "Ford",
        "Jaguar",
        "Land Rover",
        "Mercedes",
        "Honda",
        "Volvo",
        "Toyota",
        "Nissan",
        "Isuzu",
        "Seat",
        "Fiat",
        "Mazda",
        "General Motors",

        # Yazaki Morocco sites & industrial zones
        "Kenitra",
        "Meknes",
        "Tanger Free Zone",
        "Tangier Automotive City",

        # Ports & shipping routes
        "Tanger Med",
        "Tanger port",
        "Shanghai port",
        "Suez Canal",
        "Red Sea",
        "port congestion",
        "port strike",

        # Supply chain & industry
        "supply chain",
        "wiring harness",
        "automotive wiring",
        "automotive industry",
        "electric vehicle",
        "semiconductor shortage",

        # Macroeconomic & commodities
        "copper price",

        # Geopolitical & regional risk
        "Morocco Algeria",
        "Morocco EU",

        # Operational & safety risk
        "factory fire",
        "Morocco earthquake",

        # Workforce
        "strike",
        "layoffs",
    ],
    "fr": [
        "Yazaki",

        "Fujikura",
        "Sumitomo Electric",
        "Leoni",
        "Aptiv",
        "Lear Corporation",

        "Coficab",
        "Nexans",
        "Komax",
        "Schleuniger",
        "DB Schenker",

        "Renault",
        "Dacia",
        "Stellantis",
        "Peugeot",
        "Citroën",
        "Opel",
        "Ford",
        "Jaguar",
        "Land Rover",
        "Mercedes",
        "Honda",
        "Volvo",
        "Toyota",
        "Nissan",
        "Isuzu",
        "Seat",
        "Fiat",
        "Mazda",
        "General Motors",

        "Kénitra",
        "Meknès",
        "zone franche de Tanger",
        "Tanger Automotive City",

        "Tanger Med",
        "port de Tanger",
        "port de Shanghai",
        "canal de Suez",
        "mer Rouge",
        "congestion portuaire",
        "grève portuaire",

        "chaîne d'approvisionnement",
        "faisceau de câbles",
        "câblage automobile",
        "industrie automobile",
        "véhicule électrique",
        "pénurie de semi-conducteurs",

        "prix du cuivre",

        "Maroc Algérie",
        "Maroc UE",

        "incendie usine",
        "séisme Maroc",

        "grève",
        "licenciements",
    ],
    "ar": [
        "يازاكي",                    # Yazaki

        "فوجيكورا",                  # Fujikura
        "سوميتومو",                  # Sumitomo Electric
        "ليوني",                     # Leoni
        "أبتيف",                     # Aptiv
        "لير",                       # Lear Corporation

        "كوفيكاب",                   # Coficab
        "نيكسانس",                   # Nexans
        "كوماكس",                    # Komax
        "شلوينيغر",                  # Schleuniger
        "شينكر",                     # DB Schenker

        "رينو",                      # Renault
        "داسيا",                     # Dacia
        "ستيلانتيس",                 # Stellantis
        "بيجو",                      # Peugeot
        "سيتروين",                   # Citroen
        "أوبل",                      # Opel
        "فورد",                      # Ford
        "جاكوار",                    # Jaguar
        "لاند روفر",                 # Land Rover
        "مرسيدس",                    # Mercedes
        "هوندا",                     # Honda
        "فولفو",                     # Volvo
        "تويوتا",                    # Toyota
        "نيسان",                     # Nissan
        "إيسوزو",                    # Isuzu
        "سيات",                      # Seat
        "فيات",                      # Fiat
        "مازدا",                     # Mazda
        "جنرال موتورز",              # General Motors

        "القنيطرة",                  # Kenitra
        "مكناس",                     # Meknes
        "طنجة الحرة",                # Tanger Free Zone
        "طنجة أوتوموتيف سيتي",       # Tangier Automotive City

        "ميناء طنجة المتوسط",        # Tanger Med
        "ميناء طنجة",                # Tanger port
        "ميناء شنغهاي",              # Shanghai port
        "قناة السويس",               # Suez Canal
        "البحر الأحمر",              # Red Sea
        "ازدحام الموانئ",             # port congestion
        "إضراب الميناء",             # port strike

        "سلسلة التوريد",             # supply chain
        "حزمة الأسلاك",              # wiring harness
        "أسلاك السيارات",            # automotive wiring
        "صناعة السيارات",            # automotive industry
        "السيارات الكهربائية",       # electric vehicle
        "نقص أشباه الموصلات",        # semiconductor shortage

        "سعر النحاس",                # copper price

        "المغرب والجزائر",           # Morocco Algeria
        "المغرب والاتحاد الأوروبي",  # Morocco EU

        "حريق مصنع",                 # factory fire
        "زلزال المغرب",              # Morocco earthquake

        "إضراب",                     # strike
        "تسريح",                     # layoffs
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