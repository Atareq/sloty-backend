from copy import deepcopy

REGION_GREATER_CAIRO = "GREATER_CAIRO"
REGION_ALEXANDRIA = "ALEXANDRIA"
REGION_DELTA = "DELTA"
REGION_CANAL = "CANAL"
REGION_NORTH_UPPER_EGYPT = "NORTH_UPPER_EGYPT"
REGION_CENTRAL_UPPER_EGYPT = "CENTRAL_UPPER_EGYPT"
REGION_SOUTH_UPPER_EGYPT = "SOUTH_UPPER_EGYPT"
REGION_FRONTIER = "FRONTIER"


GOVERNORATES = (
    {
        "code": "CAIRO",
        "name_en": "Cairo",
        "name_ar": "القاهرة",
        "region": REGION_GREATER_CAIRO,
        # TODO: Expand remaining non-priority centers from reviewed official sources.
        "cities": (
            {
                "code": "CAIRO_CITY",
                "name_en": "Cairo City",
                "name_ar": "مدينة القاهرة",
                "type": "capital",
            },
        ),
    },
    {
        "code": "GIZA",
        "name_en": "Giza",
        "name_ar": "الجيزة",
        "region": REGION_GREATER_CAIRO,
        "cities": (
            {
                "code": "GIZA_CITY",
                "name_en": "Giza City",
                "name_ar": "مدينة الجيزة",
                "type": "capital",
            },
        ),
    },
    {
        "code": "ALEXANDRIA",
        "name_en": "Alexandria",
        "name_ar": "الإسكندرية",
        "region": REGION_ALEXANDRIA,
        "cities": (
            {
                "code": "ALEXANDRIA_CITY",
                "name_en": "Alexandria City",
                "name_ar": "مدينة الإسكندرية",
                "type": "capital",
            },
        ),
    },
    {
        "code": "QALYUBIA",
        "name_en": "Qalyubia",
        "name_ar": "القليوبية",
        "region": REGION_GREATER_CAIRO,
        "cities": (
            {
                "code": "BENHA_CITY",
                "name_en": "Benha City",
                "name_ar": "مدينة بنها",
                "type": "capital",
            },
        ),
    },
    {
        "code": "SHARQIA",
        "name_en": "Sharqia",
        "name_ar": "الشرقية",
        "region": REGION_DELTA,
        "cities": (
            {
                "code": "ZAGAZIG_CITY",
                "name_en": "Zagazig City",
                "name_ar": "مدينة الزقازيق",
                "type": "capital",
            },
        ),
    },
    {
        "code": "DAKAHLIA",
        "name_en": "Dakahlia",
        "name_ar": "الدقهلية",
        "region": REGION_DELTA,
        "cities": (
            {
                "code": "MANSOURA_CITY",
                "name_en": "Mansoura City",
                "name_ar": "مدينة المنصورة",
                "type": "capital",
            },
        ),
    },
    {
        "code": "BEHEIRA",
        "name_en": "Beheira",
        "name_ar": "البحيرة",
        "region": REGION_DELTA,
        "cities": (
            {
                "code": "DAMANHUR_CITY",
                "name_en": "Damanhur City",
                "name_ar": "مدينة دمنهور",
                "type": "capital",
            },
        ),
    },
    {
        "code": "GHARBIA",
        "name_en": "Gharbia",
        "name_ar": "الغربية",
        "region": REGION_DELTA,
        "cities": (
            {
                "code": "TANTA_CITY",
                "name_en": "Tanta City",
                "name_ar": "مدينة طنطا",
                "type": "capital",
            },
        ),
    },
    {
        "code": "MONUFIA",
        "name_en": "Monufia",
        "name_ar": "المنوفية",
        "region": REGION_DELTA,
        "cities": (
            {
                "code": "SHEBIN_EL_KOM_CITY",
                "name_en": "Shebin El Kom City",
                "name_ar": "مدينة شبين الكوم",
                "type": "capital",
            },
        ),
    },
    {
        "code": "KAFR_EL_SHEIKH",
        "name_en": "Kafr El Sheikh",
        "name_ar": "كفر الشيخ",
        "region": REGION_DELTA,
        "cities": (
            {
                "code": "KAFR_EL_SHEIKH_CITY",
                "name_en": "Kafr El Sheikh City",
                "name_ar": "مدينة كفر الشيخ",
                "type": "capital",
            },
        ),
    },
    {
        "code": "DAMIETTA",
        "name_en": "Damietta",
        "name_ar": "دمياط",
        "region": REGION_DELTA,
        "cities": (
            {
                "code": "DAMIETTA_CITY",
                "name_en": "Damietta City",
                "name_ar": "مدينة دمياط",
                "type": "capital",
            },
        ),
    },
    {
        "code": "PORT_SAID",
        "name_en": "Port Said",
        "name_ar": "بورسعيد",
        "region": REGION_CANAL,
        "cities": (
            {
                "code": "PORT_SAID_CITY",
                "name_en": "Port Said City",
                "name_ar": "مدينة بورسعيد",
                "type": "capital",
            },
        ),
    },
    {
        "code": "ISMAILIA",
        "name_en": "Ismailia",
        "name_ar": "الإسماعيلية",
        "region": REGION_CANAL,
        "cities": (
            {
                "code": "ISMAILIA_CITY",
                "name_en": "Ismailia City",
                "name_ar": "مدينة الإسماعيلية",
                "type": "capital",
            },
        ),
    },
    {
        "code": "SUEZ",
        "name_en": "Suez",
        "name_ar": "السويس",
        "region": REGION_CANAL,
        "cities": (
            {
                "code": "SUEZ_CITY",
                "name_en": "Suez City",
                "name_ar": "مدينة السويس",
                "type": "capital",
            },
        ),
    },
    {
        "code": "BENI_SUEF",
        "name_en": "Beni Suef",
        "name_ar": "بني سويف",
        "region": REGION_NORTH_UPPER_EGYPT,
        "cities": (
            {
                "code": "BENI_SUEF_CITY",
                "name_en": "Beni Suef City",
                "name_ar": "مدينة بني سويف",
                "type": "capital",
            },
        ),
    },
    {
        "code": "FAYOUM",
        "name_en": "Fayoum",
        "name_ar": "الفيوم",
        "region": REGION_NORTH_UPPER_EGYPT,
        "cities": (
            {
                "code": "FAYOUM_CITY",
                "name_en": "Fayoum City",
                "name_ar": "مدينة الفيوم",
                "type": "capital",
            },
        ),
    },
    {
        "code": "MINYA",
        "name_en": "Minya",
        "name_ar": "المنيا",
        "region": REGION_NORTH_UPPER_EGYPT,
        "cities": (
            {
                "code": "ABU_QIRQAS",
                "name_en": "Abu Qirqas",
                "name_ar": "أبو قرقاص",
                "type": "markaz",
            },
            {
                "code": "EL_IDWA",
                "name_en": "El Idwa",
                "name_ar": "العدوة",
                "type": "markaz",
            },
            {
                "code": "MINYA_MARKAZ",
                "name_en": "Minya Markaz",
                "name_ar": "مركز المنيا",
                "type": "markaz",
            },
            {
                "code": "MINYA_1",
                "name_en": "Minya First",
                "name_ar": "أول المنيا",
                "type": "qism",
            },
            {
                "code": "MINYA_2",
                "name_en": "Minya Second",
                "name_ar": "ثان المنيا",
                "type": "qism",
            },
            {
                "code": "MINYA_3",
                "name_en": "Minya Third",
                "name_ar": "ثالث المنيا",
                "type": "qism",
            },
            {
                "code": "BENI_MAZAR",
                "name_en": "Beni Mazar",
                "name_ar": "بني مزار",
                "type": "markaz",
            },
            {
                "code": "DEIR_MAWAS",
                "name_en": "Deir Mawas",
                "name_ar": "دير مواس",
                "type": "markaz",
            },
            {
                "code": "NEW_MINYA",
                "name_en": "New Minya",
                "name_ar": "المنيا الجديدة",
                "type": "new_city",
            },
            {
                "code": "MAGHAGHA",
                "name_en": "Maghagha",
                "name_ar": "مغاغة",
                "type": "markaz",
            },
            {
                "code": "MALLAWI_MARKAZ",
                "name_en": "Mallawi Markaz",
                "name_ar": "مركز ملوي",
                "type": "markaz",
            },
            {
                "code": "MALLAWI_CITY",
                "name_en": "Mallawi City",
                "name_ar": "مدينة ملوي",
                "type": "qism",
            },
            {
                "code": "MATAI",
                "name_en": "Matai",
                "name_ar": "مطاي",
                "type": "markaz",
            },
            {
                "code": "SAMALUT_EAST",
                "name_en": "Samalut East",
                "name_ar": "سمالوط شرق",
                "type": "markaz",
            },
            {
                "code": "SAMALUT_WEST",
                "name_en": "Samalut West",
                "name_ar": "سمالوط غرب",
                "type": "markaz",
            },
        ),
    },
    {
        "code": "ASSIUT",
        "name_en": "Assiut",
        "name_ar": "أسيوط",
        "region": REGION_CENTRAL_UPPER_EGYPT,
        "cities": (
            {
                "code": "ABNUB",
                "name_en": "Abnub",
                "name_ar": "أبنوب",
                "type": "markaz",
            },
            {
                "code": "ABU_TIG_CITY",
                "name_en": "Abu Tig",
                "name_ar": "أبو تيج",
                "type": "qism",
            },
            {
                "code": "ABU_TIG_MARKAZ",
                "name_en": "Abu Tig Markaz",
                "name_ar": "مركز أبو تيج",
                "type": "markaz",
            },
            {
                "code": "EL_BADARI",
                "name_en": "El Badari",
                "name_ar": "البداري",
                "type": "markaz",
            },
            {
                "code": "EL_FATEH",
                "name_en": "El Fateh",
                "name_ar": "الفتح",
                "type": "markaz",
            },
            {
                "code": "EL_GHANAYEM",
                "name_en": "El Ghanayem",
                "name_ar": "الغنايم",
                "type": "markaz",
            },
            {
                "code": "EL_QUSIYA",
                "name_en": "El Qusiya",
                "name_ar": "القوصية",
                "type": "markaz",
            },
            {
                "code": "ASSIUT_MARKAZ",
                "name_en": "Assiut Markaz",
                "name_ar": "مركز أسيوط",
                "type": "markaz",
            },
            {
                "code": "ASSIUT_1",
                "name_en": "Assiut First",
                "name_ar": "أول أسيوط",
                "type": "qism",
            },
            {
                "code": "ASSIUT_2",
                "name_en": "Assiut Second",
                "name_ar": "ثان أسيوط",
                "type": "qism",
            },
            {
                "code": "DAIRUT",
                "name_en": "Dairut",
                "name_ar": "ديروط",
                "type": "markaz",
            },
            {
                "code": "NEW_ASSIUT",
                "name_en": "New Assiut",
                "name_ar": "أسيوط الجديدة",
                "type": "new_city",
            },
            {
                "code": "MANFALUT",
                "name_en": "Manfalut",
                "name_ar": "منفلوط",
                "type": "markaz",
            },
            {
                "code": "SAHEL_SELIM",
                "name_en": "Sahel Selim",
                "name_ar": "ساحل سليم",
                "type": "markaz",
            },
            {
                "code": "SIDFA",
                "name_en": "Sidfa",
                "name_ar": "صدفا",
                "type": "markaz",
            },
        ),
    },
    {
        "code": "SOHAG",
        "name_en": "Sohag",
        "name_ar": "سوهاج",
        "region": REGION_CENTRAL_UPPER_EGYPT,
        "cities": (
            {
                "code": "AKHMIM",
                "name_en": "Akhmim",
                "name_ar": "أخميم",
                "type": "markaz",
            },
            {
                "code": "EL_BALYANA",
                "name_en": "El Balyana",
                "name_ar": "البلينا",
                "type": "markaz",
            },
            {
                "code": "EL_KAWTHAR",
                "name_en": "El Kawthar",
                "name_ar": "الكوثر",
                "type": "qism",
            },
            {
                "code": "EL_MARAGHA",
                "name_en": "El Maragha",
                "name_ar": "المراغة",
                "type": "markaz",
            },
            {
                "code": "EL_MUNSHA",
                "name_en": "El Munsha",
                "name_ar": "المنشأة",
                "type": "markaz",
            },
            {
                "code": "ASERAT",
                "name_en": "Aserat",
                "name_ar": "العسيرات",
                "type": "markaz",
            },
            {
                "code": "DAR_EL_SALAM",
                "name_en": "Dar El Salam",
                "name_ar": "دار السلام",
                "type": "markaz",
            },
            {
                "code": "GIRGA_CITY",
                "name_en": "Girga City",
                "name_ar": "مدينة جرجا",
                "type": "qism",
            },
            {
                "code": "GIRGA_MARKAZ",
                "name_en": "Girga Markaz",
                "name_ar": "مركز جرجا",
                "type": "markaz",
            },
            {
                "code": "JUHAYNAH_WEST",
                "name_en": "Juhaynah West",
                "name_ar": "جهينة الغربية",
                "type": "markaz",
            },
            {
                "code": "NEW_AKHMIM",
                "name_en": "New Akhmim",
                "name_ar": "أخميم الجديدة",
                "type": "new_city",
            },
            {
                "code": "NEW_SOHAG",
                "name_en": "New Sohag",
                "name_ar": "سوهاج الجديدة",
                "type": "new_city",
            },
            {
                "code": "SAQULTAH",
                "name_en": "Saqultah",
                "name_ar": "ساقلتة",
                "type": "markaz",
            },
            {
                "code": "SOHAG_MARKAZ",
                "name_en": "Sohag Markaz",
                "name_ar": "مركز سوهاج",
                "type": "markaz",
            },
            {
                "code": "SOHAG_1",
                "name_en": "Sohag First",
                "name_ar": "أول سوهاج",
                "type": "qism",
            },
            {
                "code": "SOHAG_2",
                "name_en": "Sohag Second",
                "name_ar": "ثان سوهاج",
                "type": "qism",
            },
            {
                "code": "TAHTA_CITY",
                "name_en": "Tahta City",
                "name_ar": "مدينة طهطا",
                "type": "qism",
            },
            {
                "code": "TAHTA_MARKAZ",
                "name_en": "Tahta Markaz",
                "name_ar": "مركز طهطا",
                "type": "markaz",
            },
            {"code": "TIMA", "name_en": "Tima", "name_ar": "طما", "type": "markaz"},
        ),
    },
    {
        "code": "QENA",
        "name_en": "Qena",
        "name_ar": "قنا",
        "region": REGION_SOUTH_UPPER_EGYPT,
        "cities": (
            {
                "code": "ABU_TESHT",
                "name_en": "Abu Tesht",
                "name_ar": "أبوتشت",
                "type": "markaz",
            },
            {
                "code": "DISHNA",
                "name_en": "Dishna",
                "name_ar": "دشنا",
                "type": "markaz",
            },
            {
                "code": "EL_WAQF",
                "name_en": "El Waqf",
                "name_ar": "الوقف",
                "type": "markaz",
            },
            {
                "code": "FARSHUT",
                "name_en": "Farshut",
                "name_ar": "فرشوط",
                "type": "markaz",
            },
            {
                "code": "NAG_HAMMADI",
                "name_en": "Nag Hammadi",
                "name_ar": "نجع حمادي",
                "type": "markaz",
            },
            {
                "code": "NAQADA",
                "name_en": "Naqada",
                "name_ar": "نقادة",
                "type": "markaz",
            },
            {
                "code": "NEW_QENA",
                "name_en": "New Qena",
                "name_ar": "قنا الجديدة",
                "type": "new_city",
            },
            {
                "code": "QENA_CITY",
                "name_en": "Qena City",
                "name_ar": "مدينة قنا",
                "type": "qism",
            },
            {
                "code": "QENA_MARKAZ",
                "name_en": "Qena Markaz",
                "name_ar": "مركز قنا",
                "type": "markaz",
            },
            {"code": "QIFT", "name_en": "Qift", "name_ar": "قفط", "type": "markaz"},
            {"code": "QUS", "name_en": "Qus", "name_ar": "قوص", "type": "markaz"},
        ),
    },
    {
        "code": "LUXOR",
        "name_en": "Luxor",
        "name_ar": "الأقصر",
        "region": REGION_SOUTH_UPPER_EGYPT,
        "cities": (
            {
                "code": "LUXOR_CITY",
                "name_en": "Luxor City",
                "name_ar": "مدينة الأقصر",
                "type": "capital",
            },
        ),
    },
    {
        "code": "ASWAN",
        "name_en": "Aswan",
        "name_ar": "أسوان",
        "region": REGION_SOUTH_UPPER_EGYPT,
        "cities": (
            {
                "code": "ASWAN_CITY",
                "name_en": "Aswan City",
                "name_ar": "مدينة أسوان",
                "type": "capital",
            },
        ),
    },
    {
        "code": "RED_SEA",
        "name_en": "Red Sea",
        "name_ar": "البحر الأحمر",
        "region": REGION_FRONTIER,
        "cities": (
            {
                "code": "HURGHADA_CITY",
                "name_en": "Hurghada City",
                "name_ar": "مدينة الغردقة",
                "type": "capital",
            },
        ),
    },
    {
        "code": "NEW_VALLEY",
        "name_en": "New Valley",
        "name_ar": "الوادي الجديد",
        "region": REGION_FRONTIER,
        "cities": (
            {
                "code": "KHARGA_CITY",
                "name_en": "Kharga City",
                "name_ar": "مدينة الخارجة",
                "type": "capital",
            },
        ),
    },
    {
        "code": "MATROUH",
        "name_en": "Matrouh",
        "name_ar": "مطروح",
        "region": REGION_FRONTIER,
        "cities": (
            {
                "code": "MARSA_MATROUH_CITY",
                "name_en": "Marsa Matrouh City",
                "name_ar": "مدينة مرسى مطروح",
                "type": "capital",
            },
        ),
    },
    {
        "code": "NORTH_SINAI",
        "name_en": "North Sinai",
        "name_ar": "شمال سيناء",
        "region": REGION_FRONTIER,
        "cities": (
            {
                "code": "ARISH_CITY",
                "name_en": "Arish City",
                "name_ar": "مدينة العريش",
                "type": "capital",
            },
        ),
    },
    {
        "code": "SOUTH_SINAI",
        "name_en": "South Sinai",
        "name_ar": "جنوب سيناء",
        "region": REGION_FRONTIER,
        "cities": (
            {
                "code": "EL_TOR_CITY",
                "name_en": "El Tor City",
                "name_ar": "مدينة الطور",
                "type": "capital",
            },
        ),
    },
)

_GOVERNORATE_BY_CODE = {
    governorate["code"]: governorate for governorate in GOVERNORATES
}
_CITIES_BY_GOVERNORATE = {
    governorate["code"]: tuple(governorate["cities"]) for governorate in GOVERNORATES
}
_CITY_BY_CODE = {
    city["code"]: city for governorate in GOVERNORATES for city in governorate["cities"]
}


def get_governorate_choices():
    return [
        (governorate["code"], governorate["name_en"]) for governorate in GOVERNORATES
    ]


def get_all_city_choices():
    return [
        (city["code"], city["name_en"])
        for governorate in GOVERNORATES
        for city in governorate["cities"]
    ]


def get_city_choices(governorate_code):
    return [
        (city["code"], city["name_en"])
        for city in _CITIES_BY_GOVERNORATE.get(governorate_code, ())
    ]


def is_valid_governorate(governorate_code):
    return governorate_code in _GOVERNORATE_BY_CODE


def is_valid_city(city_code):
    return city_code in _CITY_BY_CODE


def is_valid_city_for_governorate(governorate_code, city_code):
    return any(
        city["code"] == city_code
        for city in _CITIES_BY_GOVERNORATE.get(governorate_code, ())
    )


def get_location_payload():
    governorates = []
    for governorate in deepcopy(GOVERNORATES):
        governorate["cities"] = list(governorate["cities"])
        governorates.append(governorate)
    return {"governorates": governorates}
