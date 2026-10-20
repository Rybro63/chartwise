"""Realistic synthetic visits with an answer key.

A visit is built from one patient's (synthetic) Synthea record and reads like a real outpatient
conversation rather than a template: small talk, a history given out of order with a spoken
self-correction, pertinent negatives, medication reconciliation where a dose changes (sometimes
"forty... sorry, twenty milligrams"), a drug the patient quietly stopped, a drug that is
considered and declined, a relative's medication, vitals with a recheck, orders, referrals,
filler words, crosstalk and speech-to-text misspellings of drug names.

Every visit comes with "facts", the answer key a faithful note is graded against
(services/note-worker/eval/grading.py):

    {"schema": "visit-facts-v1",
     "expected":  [{"id", "kind", "text", "all": [[alternatives], ...]}],
     "forbidden": [{"id", "kind", "text", "all": [[alternatives], ...], "unless": [cues]}]}

An expected fact is present when one sentence of the note contains at least one alternative from
every group in "all". A forbidden fact is violated when one sentence matches "all" and contains
none of the "unless" cues (for example "sertraline" + "start" without "declined" or "hold off").
A forbidden fact with "sections" only counts in those note sections: the history may say the
patient was taking 10 mg, but the plan must not keep them on it.
Keywords are lower case and already normalised the way the grader normalises notes: numbers as
digits, "mg" for milligrams, blood pressure as "148/92".

Synthetic data only: everything here is derived from Synthea records and fixed phrase lists.
Standard library only.
"""

import random
import re

SCHEMA = "visit-facts-v1"

# Words in Synthea medication names that are not the drug.
_NOT_DRUG = {"hr", "ml", "mg", "oral", "tablet", "capsule", "extended", "release", "injection", "solution",
             "inhaler", "metered", "dose", "actuat", "auto", "injector", "prefilled", "syringe", "topical", "cream",
             "nda", "day", "pack", "suspension", "injectable", "chewable", "delayed", "hydrochloride", "abuse",
             "deterrent", "sodium", "succinate", "tartrate", "potassium", "hyclate", "bitartrate", "isophane",
             "human", "regular", "mucosal", "spray", "transdermal", "system", "inhalation", "powder", "dry",
             "propionate", "acid", "maleate", "inert", "ingredients"}

# How a clinician (and so a note) may refer to a drug, beyond its generic name.
ALIASES = {
    "hydrochlorothiazide": ["hydrochlorothiazide", "hctz"],
    "nitroglycerin": ["nitroglycerin", "nitro", "ntg"],
    "vitamin": ["vitamin", "b12", "cyanocobalamin"],
    "ferrous": ["ferrous", "iron"],
    "alendronic": ["alendron", "fosamax"],
    "acetaminophen": ["acetaminophen", "tylenol", "paracetamol"],
    "epinephrine": ["epinephrine", "epipen", "epi-pen"],
    "insulin": ["insulin"],
    "ethinyl": ["ethinyl", "birth control", "contracepti", "ocp"],
    "dienogest": ["dienogest", "birth control", "contracepti", "ocp"],
    "chlorpheniramine": ["chlorpheniramine", "antihistamine"],
}

# How people say drugs whose generic name nobody speaks aloud.
SPOKEN_NAMES = {"ferrous": "iron pills", "ethinyl": "birth control pill", "dienogest": "birth control pill",
                "vitamin": "B12 shots", "nitroglycerin": "nitro spray", "alendronic": "alendronate",
                "epinephrine": "EpiPen", "insulin": "insulin"}

# Drugs whose dose a clinician commonly titrates, with the next step up.
TITRATION = {
    "lisinopril": {"10": "20", "20": "40", "5": "10"},
    "amlodipine": {"2.5": "5", "5": "10"},
    "simvastatin": {"10": "20", "20": "40"},
    "atorvastatin": {"20": "40", "40": "80", "10": "20"},
    "pravastatin": {"40": "80", "20": "40"},
    "losartan": {"50": "100", "25": "50"},
    "metformin": {"500": "1000"},
    "sertraline": {"50": "100", "100": "150"},
    "metoprolol": {"50": "100", "25": "50"},
}

NUMBER_WORDS = {"1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven", "8": "eight", "10": "ten",
                "20": "twenty", "25": "twenty-five", "40": "forty", "50": "fifty", "80": "eighty", "100": "one hundred"}

FILLERS = ["um, ", "uh, ", "so, ", "you know, ", "honestly, ", "well, "]
RELATIVES = ["sister", "brother", "husband", "wife", "mother", "neighbor"]
RELATIVE_WORDS = ["sister", "brother", "husband", "wife", "spouse", "mother", "neighbor", "relative", "family"]
RELATIVE_DRUGS = ["metformin", "warfarin", "sertraline", "levothyroxine", "gabapentin", "atorvastatin", "tamsulosin"]

SMALL_TALK = [  # (line, keyword a note must not contain, reply)
    ("Patient: Traffic was terrible, they're redoing the bridge on Fifth Street again.", "bridge", "Oh no. Well, I'm glad you made it."),
    ("Patient: My granddaughter had her first soccer game on Saturday, she scored twice.", "soccer", "Ha, that's great."),
    ("Patient: We just got back from visiting my son in Phoenix, it was so hot.", "phoenix", "I bet. Welcome back."),
    ("Patient: My tomatoes are finally coming in, I'll have to bring you some.", "tomato", "I won't say no to that."),
    ("Patient: We adopted a puppy last month, so I'm not sleeping much, ha.", "puppy", "Oh, fun. Exhausting, but fun."),
    ("Patient: I almost didn't make it, my car wouldn't start this morning.", "car wouldn", "Oh no. Well, I'm glad you made it."),
]

SOCIAL_HISTORY = [  # (question, answer, fact, groups)
    ("Are you still smoking at all?", "No, I quit about five years ago.", "former smoker, quit 5 years ago",
     [["quit", "former smoker", "ex-smoker", "former tobacco"]]),
    ("Do you smoke?", "Never have.", "never smoker", [["never smok", "non-smoker", "nonsmoker", "denies tobacco", "no tobacco"]]),
    ("How much are you drinking these days?", "Just a glass of wine on the weekends.", "drinks wine on weekends",
     [["wine", "alcohol", "drink"]]),
]

# A second, smaller problem raised at the end ("while I'm here..."), with what the note must record.
MINOR_ISSUES = [
    ("While I'm here, I've had this itchy patch on my elbow for a while now.",
     "That looks like eczema. Try an over-the-counter hydrocortisone cream twice a day.",
     [("eczema or rash on the elbow", [["elbow", "eczema", "rash", "dermatitis"]]),
      ("hydrocortisone cream", [["hydrocortisone"]])], "hydrocortisone"),
    ("Oh, and my right knee clicks when I go up stairs. It doesn't really hurt.",
     "That's usually just wear and tear. Ice it if it gets sore, and we'll keep an eye on it.",
     [("right knee clicking", [["knee"]])], None),
    ("I've still got a dry cough from that cold last month.",
     "Your lungs sound clear, so it should settle. Honey and fluids, and tell me if it doesn't go away.",
     [("lingering dry cough after a cold", [["cough"]])], None),
    ("Can I get my flu shot today?",
     "Yes, the nurse will give it to you on the way out.",
     [("influenza vaccine today", [["flu", "influenza"]])], None),
]

FOLLOW_UPS = {
    "2 weeks": ("two weeks", ["2 week", "14 day"]),
    "4 weeks": ("four weeks", ["4 week", "1 month", "one month"]),
    "3 months": ("three months", ["3 month", "12 week"]),
    "6 months": ("six months", ["6 month"]),
}

STOP_REASONS = ["it made me dizzy", "my stomach didn't agree with it", "I read something online that scared me",
                "I ran out and never refilled it", "it was making me tired"]

# Order matters: the first complaint matching an active condition is the reason for the visit.
COMPLAINTS = [
    {
        "match": ["sinusitis"],
        "reason": "I've had this sinus pressure that just won't go away.",
        "duration": ("ten days", "two weeks"),  # said, then corrected to
        "symptoms": [
            ("My whole face hurts, right here under my eyes.", "facial pain or pressure", [["face", "facial", "sinus pressure", "sinus pain", "maxillary"]]),
            ("And there's this thick yellow-green stuff when I blow my nose.", "purulent nasal discharge", [["discharge", "drainage", "mucus", "purulent", "yellow", "green"]]),
        ],
        "negative": ("Any fever or chills?", "No fever, I checked a few times.", "denies fever", [["fever", "febrile"]]),
        "severity": True,
        "exam": ("There's tenderness when I press over your cheeks, and your nasal passages are pretty swollen.", "maxillary sinus tenderness", [["tender"]]),
        "start": [("amoxicillin", "875", "twice a day for ten days", "antibiotic"), ("doxycycline", "100", "twice a day for ten days", "antibiotic")],
        "advice": ("Use saline rinses twice a day too, that helps a lot.", "saline nasal rinses", [["saline", "nasal rinse", "nasal irrigation", "neti"]]),
        "orders": [],
    },
    {
        "match": ["hypertension"],
        "reason": "I'm here to follow up on my blood pressure.",
        "duration": None,
        "symptoms": [
            ("I've been getting these headaches in the morning, like at the back of my head.", "morning headaches", [["headache"]]),
        ],
        "negative": ("Any chest pain or shortness of breath?", "No, nothing like that.", "denies chest pain", [["chest pain"]]),
        "exam": ("Heart has a regular rhythm, no murmurs, and no swelling in your ankles.", "no ankle edema", [["edema", "swelling"]]),
        "start": [("amlodipine", "5", "once a day", "blood pressure")],
        "advice": ("Try to cut back on the salt, and keep a log of your readings at home.", "low-salt diet and home blood pressure log",
                   [["salt", "sodium", "home blood pressure", "home bp", "log"]]),
        "orders": [("We'll check your kidney function and electrolytes with a basic metabolic panel.", "basic metabolic panel",
                    [["metabolic panel", "bmp", "cmp", "electrolytes", "kidney function", "renal function"]])],
    },
    {
        "match": ["prediabetes", "diabetes", "hyperglycemia"],
        "reason": "I'm here for my sugar check.",
        "duration": ("a week or so", "about a month"),
        "symptoms": [
            ("I've been really thirsty, like all the time.", "increased thirst", [["thirst", "polydipsia"]]),
            ("And I'm up two or three times a night to use the bathroom.", "nocturia", [["nocturia", "urinat", "bathroom", "polyuria", "at night"]]),
        ],
        "negative": ("Any changes in your vision?", "No, my eyes are fine.", "denies vision changes", [["vision", "visual", "blurr"]]),
        "exam": ("Your feet look good, sensation is normal with the monofilament.", "normal foot sensation on monofilament exam",
                 [["monofilament", "foot", "feet", "sensation"]]),
        "start": [],
        "decline": ("metformin", "We could start you on metformin to help bring your sugar down.",
                    "I'd really rather try fixing my diet first, if that's okay."),
        "advice": ("Cut back on the sweet drinks and try to walk thirty minutes most days.", "diet and exercise counselling",
                   [["diet", "exercise", "walk", "lifestyle", "sugar-sweetened", "sweet drink"]]),
        "orders": [("We'll recheck your A1C today.", "hemoglobin A1C", [["a1c", "hba1c", "hemoglobin a1c", "glycated"]])],
    },
    {
        "match": ["ischemic heart", "myocardial infarction", "coronary"],
        "reason": "I've been getting some tightness in my chest again.",
        "duration": ("a couple of days", "about a week"),
        "symptoms": [
            ("It comes on when I walk up the hill to my mailbox, then goes away if I stop and rest.", "exertional chest tightness relieved by rest",
             [["exert", "walking", "uphill", "hill", "activity"]]),
        ],
        "negative": ("Does it ever happen when you're just sitting there?", "No, only when I'm moving around.", "no chest pain at rest",
                     [["at rest", "rest"]]),
        "exam": ("Heart sounds are regular, lungs are clear, no swelling in your legs.", "lungs clear, no leg edema", [["lungs", "clear", "edema"]]),
        "start": [],
        "advice": ("If it doesn't go away with rest or the nitro, you call 911, okay?", "return precautions: call 911 if pain persists",
                   [["911", "emergency", "er ", "emergency department"]]),
        "orders": [("I want you to see cardiology, and we'll get a stress test set up.", "cardiology referral and stress test",
                    [["cardiolog", "stress test"]])],
    },
    {
        "match": ["anemia"],
        "reason": "I've been so tired lately.",
        "duration": ("a few weeks", "two months"),
        "symptoms": [
            ("I'm exhausted by the afternoon, and I get winded going up the stairs.", "fatigue and exertional shortness of breath",
             [["fatigue", "tired", "exhaust", "winded", "short of breath", "dyspnea"]]),
        ],
        "negative": ("Any black or bloody stools?", "No, nothing like that.", "denies melena or blood in stool",
                     [["black", "bloody", "melena", "blood in", "hematochezia", "stool"]]),
        "exam": ("You look a little pale, but your heart and lungs sound fine.", "pallor", [["pale", "pallor"]]),
        "start": [],
        "advice": None,
        "orders": [("Let's check a CBC and your iron levels.", "CBC and iron studies", [["cbc", "blood count", "iron", "ferritin"]])],
    },
    {
        "match": ["hyperlipidemia", "hypertriglyceridemia", "metabolic syndrome"],
        "reason": "I wanted to go over my cholesterol.",
        "duration": None,
        "symptoms": [
            ("I've been walking more, but my legs ache sometimes, like the muscles.", "intermittent leg muscle aches",
             [["muscle", "myalgia", "ache", "leg"]]),
        ],
        "negative": ("Any weakness or dark urine?", "No.", "denies weakness or dark urine", [["weakness", "dark urine"]]),
        "exam": ("Exam looks normal today.", "normal exam", [["normal", "unremarkable"]]),
        "start": [("atorvastatin", "20", "at night", "cholesterol")],
        "advice": None,
        "orders": [("We'll do a fasting lipid panel before your next visit.", "fasting lipid panel", [["lipid"]])],
    },
    {
        "match": ["back pain", "chronic pain", "neck pain"],
        "reason": "My lower back has been killing me.",
        "duration": ("two weeks", "about three weeks"),
        "symptoms": [
            ("It started after I moved a dresser, and it's worse when I bend over.", "low back pain after lifting, worse with bending",
             [["back"]]),
        ],
        "severity": True,
        "negative": ("Any numbness in your legs, or trouble with your bladder?", "No, none of that.", "denies numbness or bladder symptoms",
                     [["numb", "bladder", "bowel", "saddle"]]),
        "exam": ("You're tender along the lower back muscles, and the leg raise test is negative.", "paraspinal tenderness, negative straight leg raise",
                 [["tender", "straight leg", "leg raise"]]),
        "start": [("cyclobenzaprine", "5", "at bedtime as needed", "muscle relaxer")],
        "advice": ("Keep moving as much as you can, bed rest makes it worse.", "stay active", [["active", "moving", "bed rest", "activity"]]),
        "orders": [("I'm going to send a referral to physical therapy.", "physical therapy referral", [["physical therapy", "pt ", "physiotherapy"]])],
    },
    {
        "match": ["migraine"],
        "reason": "My migraines are back.",
        "duration": ("twice a week", "three times a week"),
        "duration_question": "How often are you getting them?",
        "symptoms": [
            ("When they hit, the light really bothers me and I feel sick to my stomach.", "photophobia and nausea with headaches",
             [["light", "photophobia", "nausea"]]),
        ],
        "severity": True,
        "negative": ("Any weakness or numbness with them, or vision loss?", "No.", "denies focal neurologic symptoms",
                     [["weakness", "numbness", "neurolog", "vision loss", "focal"]]),
        "exam": ("Your neuro exam is completely normal.", "normal neurologic exam", [["neuro", "normal"]]),
        "start": [("sumatriptan", "50", "at the start of a headache, can repeat once", "migraine")],
        "advice": ("Keep a headache diary so we can see what triggers them.", "headache diary", [["diary", "trigger"]]),
        "orders": [],
    },
    {
        "match": ["stress", "anxiety", "depress", "social isolation"],
        "reason": "I've been really stressed out and not sleeping.",
        "duration": ("a month", "since the summer"),
        "symptoms": [
            ("I lie awake for hours, and then I'm useless at work.", "insomnia affecting work", [["sleep", "insomnia", "awake"]]),
        ],
        "negative": ("Any thoughts of hurting yourself?", "No, nothing like that.", "denies thoughts of self-harm",
                     [["harm", "suicid", "safety"]]),
        "exam": ("You seem tired, but your mood and thinking are clear.", "tired-appearing, thought process clear", [["tired", "mood", "affect", "thought"]]),
        "start": [],
        "decline": ("sertraline", "One option is a daily medicine like sertraline.",
                    "I don't think I want to be on something like that right now."),
        "advice": None,
        "orders": [("I'll put in a referral for counseling, they're really good.", "counseling referral",
                    [["counsel", "therap", "behavioral health", "psycholog", "mental health"]])],
    },
    {
        "match": ["obesity", "body mass index"],
        "reason": "I want to talk about my weight.",
        "duration": ("this year", "since last spring"),
        "symptoms": [
            ("I've put on about fifteen pounds and my knees are feeling it.", "15 lb weight gain with knee discomfort",
             [["weight", "15 lb", "15 pound", "pounds"]]),
        ],
        "negative": ("Any snoring or waking up gasping?", "My wife says I don't snore.", "no reported snoring",
                     [["snor", "apnea", "gasp"]]),
        "exam": ("Knees have full range of motion, no swelling.", "knees with full range of motion", [["knee", "range of motion"]]),
        "start": [],
        "advice": None,
        "orders": [("Let me refer you to our dietitian.", "dietitian referral", [["dietitian", "nutrition"]])],
    },
]

DEFAULT_COMPLAINT = {
    "match": [],
    "reason": "Just my regular checkup, nothing major.",
    "duration": None,
    "symptoms": [("I've been feeling pretty good overall, just a little run down.", "feels well overall, mildly run down",
                  [["well", "run down", "good", "fatigue"]])],
    "negative": ("Any chest pain, trouble breathing?", "No.", "denies chest pain or dyspnea", [["chest pain", "breath", "dyspnea"]]),
    "exam": ("Lungs are clear and your heart sounds normal.", "lungs clear, heart normal", [["lung", "heart", "clear"]]),
    "start": [],
    "advice": None,
    "orders": [],
}


def parse_med(display: str) -> dict | None:
    """'Simvastatin 20 MG Oral Tablet' -> {'name': 'simvastatin', 'dose': '20', 'unit': 'mg'}."""
    name = None
    for word in re.findall(r"[A-Za-z]+", display):
        w = word.lower()
        if len(w) > 3 and w not in _NOT_DRUG:
            name = w
            break
    if not name:
        return None
    # Only a plain "N MG" strength is spoken as a dose; packs and per-ML strengths are not.
    m = re.search(r"(?<![\d.])(\d+(?:\.\d+)?) MG(?!/)", display, re.IGNORECASE)
    dose = m.group(1) if m and not display.startswith("{") else None
    return {"name": name, "dose": dose, "display": display}


def aliases(name: str) -> list[str]:
    return ALIASES.get(name, [name])


def misspell(word: str, rng: random.Random) -> str:
    """A plausible speech-to-text error: one interior vowel swapped for another."""
    positions = [i for i, c in enumerate(word) if c in "aeiou" and 0 < i < len(word) - 1]
    if len(word) < 7 or not positions:
        return word
    i = rng.choice(positions)
    return word[:i] + rng.choice([v for v in "aeiou" if v != word[i]]) + word[i + 1:]


def spoken(number: str, rng: random.Random) -> str:
    return NUMBER_WORDS[number] if number in NUMBER_WORDS and rng.random() < 0.5 else number


class _Visit:
    """Accumulates transcript lines and the answer key together, so they can't drift apart."""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.lines: list[str] = []
        self.expected: list[dict] = []
        self.forbidden: list[dict] = []

    def say(self, speaker: str, text: str, filler: float = 0.0) -> None:
        if filler and self.rng.random() < filler and not text.startswith(("And ", "Oh")):
            f = self.rng.choice(FILLERS)
            keep_case = text.startswith(("I ", "I'"))
            text = f[0].upper() + f[1:] + (text if keep_case else text[0].lower() + text[1:])
        self.lines.append(f"{speaker}: {text}")

    def expect(self, kind: str, text: str, groups: list[list[str]]) -> None:
        self.expected.append({"id": f"{kind}-{len(self.expected) + 1}", "kind": kind, "text": text, "all": groups})

    def forbid(self, kind: str, text: str, groups: list[list[str]], unless: list[str] | None = None,
               sections: list[str] | None = None) -> None:
        fact = {"id": f"{kind}-{len(self.forbidden) + 1}", "kind": kind, "text": text, "all": groups, "unless": unless or []}
        if sections:
            fact["sections"] = sections
        self.forbidden.append(fact)

    def drug(self, name: str) -> str:
        """A drug name as spoken, then as speech-to-text heard it."""
        if name in SPOKEN_NAMES:
            return SPOKEN_NAMES[name]
        return misspell(name, self.rng) if self.rng.random() < 0.25 else name


def visit(record: dict, rng: random.Random) -> dict:
    """One realistic visit for a patient record ({conditions, medications, allergies} display strings)."""
    v = _Visit(rng)
    conditions = " ".join(record["conditions"]).lower()
    allergies = " ".join(a for a in record["allergies"] if "(finding)" not in a).lower()
    meds = [m for m in (parse_med(d) for d in record["medications"]) if m]
    on = {m["name"] for m in meds}
    matches = [c for c in COMPLAINTS if any(k in conditions for k in c["match"])]
    complaint = rng.choice(matches[:2]) if matches else DEFAULT_COMPLAINT
    mentioned_drugs: set[str] = set()

    # Greeting and small talk, which a note must leave out.
    v.say("Doctor", "Hi there, good to see you again. How have you been?")
    talk, keyword, reply = rng.choice(SMALL_TALK)
    v.lines.append(talk)
    v.forbid("small_talk", f"small talk ({keyword})", [[keyword]])
    v.say("Doctor", reply)
    v.say("Doctor", "So what brings you in today?")
    v.say("Patient", complaint["reason"], filler=0.4)

    # History of present illness, with a self-corrected duration.
    v.say("Doctor", "Okay. Tell me more about that.")
    first, *rest = complaint["symptoms"]
    v.say("Patient", first[0], filler=0.3)
    v.expect("symptom", first[1], first[2])
    if complaint["duration"]:
        said, corrected = complaint["duration"]
        v.say("Doctor", complaint.get("duration_question", "How long has this been going on?"))
        if rng.random() < 0.6:
            v.say("Patient", f"Maybe {said}? No, wait, more like {corrected}.", filler=0.3)
            # Not the plan, where "for 10 days" is a course of treatment, not the history.
            v.forbid("superseded_duration", f"duration corrected from {said} to {corrected}", [[_norm_phrase(said)]],
                     unless=_duration_alternatives(corrected) + ["revis", "correct", "initial"],
                     sections=["subjective", "objective", "assessment"])
        else:
            v.say("Patient", f"I'd say {corrected}, give or take.", filler=0.3)
        v.expect("duration", f"duration {corrected}", [_duration_alternatives(corrected)])
    for line, fact, groups in rest:
        v.say("Patient", line, filler=0.3)
        v.expect("symptom", fact, groups)
    if complaint.get("severity"):
        score = rng.randint(4, 8)
        v.say("Doctor", "On a scale of one to ten, how bad does it get?")
        v.say("Patient", f"Maybe a {NUMBER_WORDS[str(score)]}. Some days worse.", filler=0.3)
        v.expect("severity", f"severity {score}/10", [[f"{score}/10", f"{score} out of 10", f"{score} of 10"]])
    question, answer, fact, groups = complaint["negative"]
    v.say("Doctor", question)
    v.say("Patient", answer)
    v.expect("pertinent_negative", fact, groups)

    # Medication reconciliation: up to three record drugs, each continued, changed or stopped.
    discussed = rng.sample(meds, k=min(len(meds), rng.randint(1, 3))) if meds else []
    changed_one = stopped_one = False
    if discussed:
        v.say("Doctor", "Let's go through your medicines.")
    for med in discussed:
        name, dose = med["name"], med["dose"]
        mentioned_drugs.add(name)
        heard = v.drug(name)
        step = TITRATION.get(name, {}).get(dose or "")
        roll = rng.random()
        if step and not changed_one and roll < 0.6:
            changed_one = True
            v.say("Doctor", f"You're on the {heard}, {spoken(dose, rng)} milligrams. Are you taking it every day?")
            v.say("Patient", "Every morning, yes.")
            if rng.random() < 0.5:
                # A spoken self-correction: the first number must not end up in the note.
                wrong = TITRATION[name].get(step, str(int(float(step) * 2)) if float(step).is_integer() else step)
                v.say("Doctor", f"Okay, I'd like to go up on that. Let's do {spoken(wrong, rng)}, sorry, no, "
                                f"{spoken(step, rng)} milligrams once a day.")
                if wrong != step:
                    v.forbid("superseded_dose", f"{name} {wrong} mg (corrected to {step} mg)",
                             [aliases(name), [f"{wrong} mg"]], unless=[f"{step} mg", "correct"])
            else:
                v.say("Doctor", f"I'd like to go up on that, from {spoken(dose, rng)} to {spoken(step, rng)} milligrams a day.")
            v.say("Patient", "Okay, if you think so.")
            v.expect("medication_change", f"{name} increased to {step} mg", [aliases(name), [f"{step} mg"]])
            v.forbid("stale_dose", f"{name} continued at the old {dose} mg", [aliases(name), [f"{dose} mg"]],
                     unless=[f"{step} mg", "increas", "from", "previous", "titrat", "prior"], sections=["plan"])
        elif not stopped_one and roll < 0.85:
            stopped_one = True
            reason = rng.choice(STOP_REASONS)
            v.say("Doctor", f"And the {heard}?")
            v.say("Patient", f"Oh, I actually stopped that one a while ago. {reason[0].upper() + reason[1:]}.", filler=0.4)
            v.say("Doctor", "Okay, I'm glad you told me. Let's leave it off for now and see how you do.")
            v.expect("medication_stopped", f"patient stopped {name}", [aliases(name), ["stop", "discontinu", "not taking",
                                                                                          "no longer", "off", "self-discontinu",
                                                                                          "held", "hold"]])
            v.forbid("stopped_med_continued", f"{name} continued although the patient stopped it", [aliases(name), ["continue"]],
                     unless=["stop", "discontinu", "not taking", "no longer", "off", "held", "hold", "not continu", "do not"])
        else:
            v.say("Doctor", f"Are you still taking the {heard}?")
            v.say("Patient", rng.choice(["Yes, every day.", "Most days. Sometimes I forget at night.",
                                         "Yeah, I missed a few last week when I was traveling."]), filler=0.3)
            if dose and rng.random() < 0.5:
                v.say("Doctor", f"Good, stay on the {spoken(dose, rng)} milligrams.")
            else:
                v.say("Doctor", "Good, keep taking it the same way.")
            v.expect("medication_continued", f"{name} continued", [aliases(name)])
    if discussed and rng.random() < 0.3:
        v.lines.append(rng.choice(["Daughter: Honestly, the evening pills get skipped a lot.",
                                   "Daughter: The evening ones get forgotten a lot, from what I see."]))
        v.say("Patient", "That's not... okay, sometimes.")
        v.expect("adherence", "family reports missed evening doses", [["miss", "skip", "forget", "adheren"]])

    if rng.random() < 0.5:
        question, answer, fact, groups = rng.choice(SOCIAL_HISTORY)
        v.say("Doctor", question)
        v.say("Patient", answer, filler=0.3)
        v.expect("social_history", fact, groups)

    # A relative's medication, which is not the patient's.
    if rng.random() < 0.35:
        drug = rng.choice([d for d in RELATIVE_DRUGS if d not in on and d not in mentioned_drugs and d != complaint.get("decline", ("",))[0]])
        relative = rng.choice(RELATIVES)
        v.say("Patient", f"My {relative} takes {drug} and swears by it. Should I be on that?", filler=0.3)
        v.say("Doctor", "That's for a different problem, it wouldn't help you.")
        # Recording that the patient asked about it is fine; saying they take it is not.
        v.forbid("relative_medication", f"{drug} is the patient's {relative}'s medication",
                 [[drug], ["takes", "taking", "continu", "current", "start", "prescrib", "on "]],
                 unless=RELATIVE_WORDS + ["asked", "question", "not indicated", "not recommend", "would not", "wouldn't",
                                          "declin", "not appropriate", "not start", "not prescrib", "not taking",
                                          "not on "])
        mentioned_drugs.add(drug)

    # Over-the-counter use.
    if rng.random() < 0.4:
        otc = rng.choice(["ibuprofen", "acetaminophen", "loratadine", "naproxen"])
        if otc not in on:
            v.say("Patient", f"Oh, and I've been taking {v.drug(otc)} from the drugstore most days.", filler=0.3)
            v.expect("otc_medication", f"takes over-the-counter {otc}", [aliases(otc)])
            mentioned_drugs.add(otc)

    allergens = [a for a in record["allergies"] if "(finding)" not in a]
    if allergens and rng.random() < 0.5:
        v.say("Doctor", f"You're still allergic to {allergens[0].split(' (')[0].lower()}, right?")
        v.say("Patient", "That's right.")

    # Vitals, sometimes with a blood pressure recheck.
    v.say("Doctor", "Let's take a look at you.")
    sys_bp, dia_bp, hr = rng.randint(118, 162), rng.randint(72, 98), rng.randint(58, 98)
    temp = rng.choice(["98.1", "98.4", "98.6", "99.0"])
    v.say("Doctor", f"Blood pressure is {sys_bp} over {dia_bp}. Heart rate {hr}. Temperature {temp}.")
    if sys_bp >= 140 and rng.random() < 0.7:
        v.say("Doctor", "That's a little high. Let me recheck it in a minute.")
        v.lines.append("[crosstalk]")
        sys_bp, dia_bp = sys_bp - rng.randint(6, 16), dia_bp - rng.randint(2, 8)
        v.say("Doctor", f"Okay, repeat is {sys_bp} over {dia_bp}.")
    v.expect("vital", f"blood pressure {sys_bp}/{dia_bp}", [[f"{sys_bp}/{dia_bp}"]])
    v.expect("vital", f"heart rate {hr}", [[str(hr)], ["heart rate", "hr", "pulse", "bpm"]])
    v.expect("vital", f"temperature {temp}", [sorted({temp, temp.removesuffix(".0")})])

    exam, fact, groups = complaint["exam"]
    v.say("Doctor", exam)
    v.expect("exam", fact, groups)

    # Plan: a new drug (respecting allergies), a declined one, orders, advice, follow-up.
    v.say("Doctor", "Okay, here's what I'm thinking.")
    penicillin_allergic = "penicillin" in allergies
    for drug, dose, how, purpose in complaint["start"]:
        if drug in on:
            continue
        if drug == "amoxicillin" and penicillin_allergic:
            v.say("Doctor", "Normally I'd use amoxicillin, but with your penicillin allergy we'll skip that.")
            v.forbid("allergy_conflict", "amoxicillin started despite penicillin allergy", [["amoxicillin"], ["start", "prescrib", "begin", "initiat", "875"]],
                     unless=["allerg", "avoid", "instead", "not ", "skip", "contraindicat"])
            mentioned_drugs.add("amoxicillin")
            continue
        heard = v.drug(drug)
        v.say("Doctor", f"I'm going to start you on {heard}, {spoken(dose, rng)} milligrams, {how}, for the {purpose}.")
        v.say("Patient", "Okay. Any side effects I should know about?", filler=0.3)
        v.say("Doctor", "Some people get an upset stomach. Call me if it's a problem.")
        v.expect("medication_started", f"start {drug} {dose} mg", [aliases(drug), [f"{dose} mg"]])
        mentioned_drugs.add(drug)
        break
    if "decline" in complaint and complaint["decline"][0] not in on:
        drug, offer, refusal = complaint["decline"]
        v.say("Doctor", offer.replace(drug, v.drug(drug)))
        v.say("Patient", refusal, filler=0.4)
        v.say("Doctor", "That's completely fair. Let's hold off and revisit it next time.")
        v.forbid("declined_medication_started", f"{drug} was offered and declined", [[drug], ["start", "begin", "initiat", "prescrib", "add"]],
                 unless=["declin", "hold", "defer", "not ", "prefer", "revisit", "consider", "discuss", "offer", "refus", "option"])
        v.expect("declined_medication", f"{drug} discussed and declined", [[drug], ["declin", "hold", "defer", "prefer", "refus", "revisit", "not "]])
        mentioned_drugs.add(drug)
    for line, fact, groups in complaint["orders"]:
        v.say("Doctor", line)
        v.expect("order", fact, groups)
    if complaint["advice"]:
        line, fact, groups = complaint["advice"]
        v.say("Doctor", line)
        v.expect("advice", fact, groups)

    if rng.random() < 0.5:
        said, reply, facts, drug = rng.choice(MINOR_ISSUES)
        if drug not in on:
            v.say("Patient", said, filler=0.3)
            v.say("Doctor", reply)
            for fact, groups in facts:
                v.expect("minor_issue", fact, groups)

    # The follow-up interval must not double as the visit's duration, or one sentence could
    # satisfy (or violate) both facts.
    taken = set()
    if complaint["duration"]:
        taken = {_norm_phrase(complaint["duration"][0]), *_duration_alternatives(complaint["duration"][1])}
    options = [k for k, (_, alts) in FOLLOW_UPS.items() if not taken & {*alts, _norm_phrase(k)}]
    follow = rng.choice(options)
    words, alternatives = FOLLOW_UPS[follow]
    v.say("Doctor", f"Let's see you back in {words}, sooner if anything gets worse.")
    v.expect("follow_up", f"follow up in {follow}", [alternatives])
    v.say("Patient", rng.choice(["Sounds good. Thanks, doc.", "Okay, thank you.", "Will do. Thanks for squeezing me in."]))

    return {"transcript": "\n".join(v.lines),
            "facts": {"schema": SCHEMA, "complaint": complaint["reason"], "expected": v.expected, "forbidden": v.forbidden}}


_WORD_NUMBERS = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "ten": 10, "twelve": 12,
                 "fifteen": 15, "thirty": 30}


def _norm_phrase(text: str) -> str:
    """'two weeks' -> '2 week' (matches '2 weeks' and '2-week' once the grader normalises them)."""
    text = text.lower().replace("about ", "").replace(" or so", "").replace("more like ", "")
    words = text.replace("a couple of", "2").replace("a few", "3").split()
    out = [str(_WORD_NUMBERS[w]) if w in _WORD_NUMBERS else w for w in words]
    phrase = " ".join(out)
    return re.sub(r"(week|month|day|year)s\b", r"\1", phrase)


def _duration_alternatives(text: str) -> list[str]:
    phrase = _norm_phrase(text)
    alts = {phrase}
    if phrase.startswith("since"):
        alts = {phrase.split()[-1]}  # "since last spring" -> "spring"
    if phrase == "1 month":
        alts |= {"4 week", "one month", "a month"}
    if phrase == "2 month":
        alts |= {"8 week"}
    if phrase == "2 week":
        alts |= {"14 day"}
    if phrase == "1 week":
        alts |= {"7 day"}
    if phrase.endswith("a week"):
        n = phrase.split()[0]
        alts |= {phrase.replace("a week", "per week"), phrase.replace("a week", "weekly"), f"{n}x/week", f"{n}x per week",
                 f"{n}x weekly", f"{n}x a week"}
    return sorted(alts)
