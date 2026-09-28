"""Drug names the safety check recognises in free text.

GENERICS covers common outpatient drugs plus everything Synthea prescribes. BRANDS maps brand names
to their generic so "Coumadin" in a note and "warfarin" in the record are recognised as the same
drug. The lists are deliberately modest and inspectable; a production system would use RxNorm.
"""

GENERICS: frozenset[str] = frozenset("""
acetaminophen adalimumab albuterol alendronate allopurinol alprazolam amiodarone amitriptyline
amlodipine amoxicillin amphetamine ampicillin anastrozole apixaban aripiprazole aspirin atenolol
atorvastatin azithromycin baclofen benazepril budesonide bupropion buspirone canagliflozin captopril
carboplatin carvedilol cefdinir cefuroxime celecoxib cephalexin cetirizine chlorthalidone ciprofloxacin
cisplatin citalopram clarithromycin clindamycin clonazepam clonidine clopidogrel clozapine colchicine
cyclobenzaprine cyclophosphamide dabigatran dapagliflozin desogestrel dexamethasone dextroamphetamine
diazepam diclofenac dicloxacillin digoxin diltiazem diphenhydramine docetaxel donepezil doxorubicin
doxycycline drospirenone duloxetine empagliflozin enalapril enoxaparin epinephrine escitalopram
esomeprazole estradiol ethinyl etonogestrel exemestane famotidine fentanyl fexofenadine finasteride
fluconazole fluoxetine fluticasone furosemide gabapentin galantamine glimepiride glipizide glyburide
haloperidol heparin hydralazine hydrochlorothiazide hydrocodone hydrocortisone hydroxychloroquine
hydroxyzine ibuprofen insulin ipratropium isoniazid ketorolac labetalol lamotrigine lansoprazole
letrozole leuprolide levetiracetam levofloxacin levonorgestrel levothyroxine liraglutide lisinopril
lithium loperamide loratadine lorazepam losartan lovastatin meloxicam memantine meperidine metformin
methadone methocarbamol methotrexate methylphenidate methylprednisolone metoclopramide metoprolol
metronidazole midazolam minoxidil mirtazapine montelukast morphine mupirocin nabumetone naloxone
naltrexone naproxen nicotine nifedipine nitrofurantoin nitroglycerin norethindrone nortriptyline
olanzapine omeprazole ondansetron oseltamivir oxybutynin oxycodone paclitaxel pantoprazole paroxetine
penicillin phenytoin pioglitazone piperacillin pravastatin prednisolone prednisone pregabalin
promethazine propranolol quetiapine ramipril ranitidine risperidone rivaroxaban rosuvastatin
salmeterol semaglutide sertraline sildenafil simvastatin sitagliptin spironolactone sulfamethoxazole
sumatriptan tacrolimus tamoxifen tamsulosin terbinafine tiotropium tizanidine topiramate tramadol
trastuzumab trazodone triamcinolone trimethoprim valacyclovir valproate valsartan vancomycin
varenicline venlafaxine verapamil warfarin zolpidem
""".split())

BRANDS: dict[str, str] = {
    "abilify": "aripiprazole", "adderall": "amphetamine", "advair": "fluticasone", "advil": "ibuprofen",
    "aldactone": "spironolactone", "aleve": "naproxen", "allegra": "fexofenadine", "ambien": "zolpidem",
    "aricept": "donepezil", "ativan": "lorazepam", "augmentin": "amoxicillin", "bactrim": "sulfamethoxazole",
    "celebrex": "celecoxib", "celexa": "citalopram", "chantix": "varenicline", "cipro": "ciprofloxacin",
    "claritin": "loratadine", "clozaril": "clozapine", "colcrys": "colchicine", "cordarone": "amiodarone",
    "coreg": "carvedilol", "coumadin": "warfarin", "cozaar": "losartan", "crestor": "rosuvastatin",
    "cymbalta": "duloxetine", "deltasone": "prednisone", "depakote": "valproate", "dilantin": "phenytoin",
    "diovan": "valsartan", "effexor": "venlafaxine", "elavil": "amitriptyline", "eliquis": "apixaban",
    "epipen": "epinephrine", "farxiga": "dapagliflozin", "flagyl": "metronidazole", "flomax": "tamsulosin",
    "flovent": "fluticasone", "fosamax": "alendronate", "glucophage": "metformin", "humalog": "insulin",
    "humira": "adalimumab", "humulin": "insulin", "imitrex": "sumatriptan", "jantoven": "warfarin",
    "januvia": "sitagliptin", "jardiance": "empagliflozin", "keflex": "cephalexin", "keppra": "levetiracetam",
    "klonopin": "clonazepam", "lamictal": "lamotrigine", "lanoxin": "digoxin", "lantus": "insulin",
    "lasix": "furosemide", "levaquin": "levofloxacin", "lexapro": "escitalopram", "lipitor": "atorvastatin",
    "lopressor": "metoprolol", "lyrica": "pregabalin", "medrol": "methylprednisolone", "mirena": "levonorgestrel",
    "mobic": "meloxicam", "motrin": "ibuprofen", "namenda": "memantine", "narcan": "naloxone",
    "neurontin": "gabapentin", "nexium": "esomeprazole", "nexplanon": "etonogestrel", "norco": "hydrocodone",
    "norvasc": "amlodipine", "novolog": "insulin", "ozempic": "semaglutide", "pacerone": "amiodarone",
    "paxil": "paroxetine", "pepcid": "famotidine", "percocet": "oxycodone", "plavix": "clopidogrel",
    "pradaxa": "dabigatran", "prilosec": "omeprazole", "prinivil": "lisinopril", "proair": "albuterol",
    "protonix": "pantoprazole", "proscar": "finasteride", "prozac": "fluoxetine", "risperdal": "risperidone",
    "ritalin": "methylphenidate", "seroquel": "quetiapine", "singulair": "montelukast", "spiriva": "tiotropium",
    "synthroid": "levothyroxine", "tamiflu": "oseltamivir", "tenormin": "atenolol", "topamax": "topiramate",
    "toprol": "metoprolol", "tylenol": "acetaminophen", "ultram": "tramadol", "valium": "diazepam",
    "valtrex": "valacyclovir", "ventolin": "albuterol", "viagra": "sildenafil", "vicodin": "hydrocodone",
    "wegovy": "semaglutide", "wellbutrin": "bupropion", "xanax": "alprazolam", "xarelto": "rivaroxaban",
    "zestril": "lisinopril", "zithromax": "azithromycin", "zocor": "simvastatin", "zofran": "ondansetron",
    "zoloft": "sertraline", "zyloprim": "allopurinol", "zyprexa": "olanzapine", "zyrtec": "cetirizine",
}

# Allergy classes: an allergy to the key implies a conflict with any drug in the set.
ALLERGY_CLASSES: dict[str, frozenset[str]] = {
    "penicillin": frozenset({"penicillin", "amoxicillin", "ampicillin", "dicloxacillin", "piperacillin"}),
    "sulfa": frozenset({"sulfamethoxazole"}),
    "sulfonamide": frozenset({"sulfamethoxazole"}),
}

ALL_NAMES: frozenset[str] = GENERICS | frozenset(BRANDS)


def canonical(name: str) -> str:
    """Maps a brand name to its generic; generics map to themselves."""
    n = name.lower()
    return BRANDS.get(n, n)
