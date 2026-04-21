"""27-country coverage for the regulatory digest.

`authorities` is a hint to the model about which bodies to search for —
it does NOT restrict the model. The model uses Google Search and decides
what's authoritative. This just sharpens the query.
"""

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class Country:
    code: str
    name: str
    region: str
    authorities: List[str]


COUNTRIES: List[Country] = [
    # South Asia
    Country("IN", "India", "South Asia",
            ["CBDT / Income Tax Department", "EPFO", "ESIC", "Ministry of Labour & Employment",
             "state Professional Tax authorities", "state Labour Welfare Fund boards"]),

    # SE Asia
    Country("SG", "Singapore", "SE Asia",
            ["IRAS", "CPF Board", "Ministry of Manpower (MOM)"]),
    Country("MY", "Malaysia", "SE Asia",
            ["LHDN (Inland Revenue)", "KWSP/EPF", "SOCSO/PERKESO", "EIS", "HRD Corp",
             "Ministry of Human Resources"]),
    Country("TH", "Thailand", "SE Asia",
            ["Revenue Department", "Social Security Office (SSO)", "Ministry of Labour"]),
    Country("VN", "Vietnam", "SE Asia",
            ["General Department of Taxation", "Vietnam Social Security (VSS)",
             "Ministry of Labour, Invalids and Social Affairs (MOLISA)"]),
    Country("PH", "Philippines", "SE Asia",
            ["BIR", "SSS", "PhilHealth", "Pag-IBIG (HDMF)", "DOLE"]),

    # GCC
    Country("AE", "UAE", "GCC",
            ["Federal Tax Authority (FTA)", "MOHRE", "GPSSA", "DIFC Employee Workplace Savings (DEWS)"]),
    Country("SA", "Saudi Arabia", "GCC",
            ["ZATCA", "GOSI", "HRSD (Ministry of Human Resources and Social Development)",
             "Mudad", "Qiwa"]),
    Country("QA", "Qatar", "GCC",
            ["General Tax Authority", "Ministry of Labour", "Wage Protection System (WPS)"]),
    Country("OM", "Oman", "GCC",
            ["Tax Authority of Oman", "PASI / Social Protection Fund",
             "Ministry of Labour"]),
    Country("KW", "Kuwait", "GCC",
            ["PIFSS (Public Institution for Social Security)",
             "Public Authority of Manpower (PAM)", "Ministry of Finance"]),
    Country("BH", "Bahrain", "GCC",
            ["NBR (National Bureau for Revenue)", "SIO (Social Insurance Organisation)",
             "LMRA", "Ministry of Labour"]),

    # East Africa
    Country("KE", "Kenya", "East Africa",
            ["KRA", "NSSF", "SHA/SHIF (formerly NHIF)", "Housing Levy",
             "NITA (industrial training levy)", "Ministry of Labour"]),
    Country("UG", "Uganda", "East Africa",
            ["URA", "NSSF Uganda", "Ministry of Gender, Labour and Social Development"]),
    Country("TZ", "Tanzania", "East Africa",
            ["TRA", "NSSF Tanzania", "PSSSF", "WCF", "SDL"]),
    Country("RW", "Rwanda", "East Africa",
            ["RRA", "RSSB (Rwanda Social Security Board)", "Ministry of Public Service and Labour"]),
    Country("MW", "Malawi", "East Africa",
            ["MRA", "Ministry of Labour"]),

    # Southern Africa
    Country("ZA", "South Africa", "Southern Africa",
            ["SARS", "UIF", "SDL", "COIDA / Department of Employment and Labour"]),
    Country("ZW", "Zimbabwe", "Southern Africa",
            ["ZIMRA", "NSSA", "Ministry of Public Service, Labour and Social Welfare"]),
    Country("ZM", "Zambia", "Southern Africa",
            ["ZRA", "NAPSA", "NHIMA", "Workers' Compensation Fund Control Board"]),
    Country("NA", "Namibia", "Southern Africa",
            ["NamRA (Namibia Revenue Agency)", "Social Security Commission",
             "Ministry of Labour"]),
    Country("MU", "Mauritius", "Southern Africa",
            ["MRA", "Ministry of Labour", "CSG (Contribution Sociale Généralisée)",
             "National Savings Fund", "HRDC training levy"]),

    # West / Central Africa
    Country("NG", "Nigeria", "West Africa",
            ["FIRS", "state IRS (LIRS, OGIRS, etc.)", "PenCom (pension)",
             "NHF (National Housing Fund)", "NSITF", "ITF (industrial training)"]),
    Country("GH", "Ghana", "West Africa",
            ["GRA", "SSNIT", "Ministry of Employment and Labour Relations"]),
    Country("CM", "Cameroon", "West Africa",
            ["DGI (Direction Générale des Impôts)", "CNPS",
             "Ministère du Travail et de la Sécurité Sociale"]),
    Country("AO", "Angola", "West Africa",
            ["AGT (Administração Geral Tributária)", "INSS Angola",
             "Ministério do Trabalho"]),
    Country("CD", "DRC", "Central Africa",
            ["DGI (Direction Générale des Impôts)", "CNSS",
             "Ministère de l'Emploi, Travail et Prévoyance Sociale"]),
]


def by_region() -> dict:
    out: dict = {}
    for c in COUNTRIES:
        out.setdefault(c.region, []).append(c)
    return out
