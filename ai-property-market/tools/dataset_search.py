# TAG: DATASET SEARCH V2.2
# Интелигентно търсене в каталога с datasets.
#
# Функции:
# - нормализиране на текст
# - кирилица / латиница / шльокавица
# - приблизително съвпадение при правописни грешки
# - търсене по няколко думи
# - relevance score 0-100
# - confidence
# - категории
# - обяснение защо е намерен резултат
# - minimum score
# - result limit
# - JSON output
# - външен config файл
#
# Използване:
#
# python tools/dataset_search.py сгради
# python tools/dataset_search.py жилищен имот
# python tools/dataset_search.py sgrada
# python tools/dataset_search.py сгрди
# python tools/dataset_search.py сгради --limit 20
# python tools/dataset_search.py сгради --min-score 70
# python tools/dataset_search.py сгради --json


import json
import sys
import re

from difflib import SequenceMatcher
from pathlib import Path


# ============================================================
# TAG: PROJECT ROOT
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent


# ============================================================
# TAG: RAW STORAGE
# ============================================================

RAW_DIR = (
    BASE_DIR /
    "storage_raw" /
    "sofiaplan"
)


# ============================================================
# TAG: CONFIG
# ============================================================

CONFIG_FILE = (
    BASE_DIR /
    "config" /
    "dataset_categories.json"
)


# ============================================================
# TAG: SEARCH SETTINGS
# ============================================================

DEFAULT_MIN_SCORE = 20

DEFAULT_LIMIT = 20


# Полетата имат различна важност.

FIELD_WEIGHTS = {

    "name": 100,

    "description": 70,

    "category": 40,

    "provider": 10
}


# ============================================================
# TAG: BULGARIAN LATIN TRANSLITERATION
# ============================================================

# Основни комбинации при писане на български с латиница.

LATIN_TO_CYRILLIC = {

    "sht": "щ",
    "sch": "щ",

    "zh": "ж",

    "ch": "ч",

    "sh": "ш",

    "yu": "ю",
    "ju": "ю",

    "ya": "я",
    "ja": "я",

    "ts": "ц",

    "a": "а",
    "b": "б",
    "v": "в",
    "g": "г",
    "d": "д",
    "e": "е",
    "z": "з",
    "i": "и",
    "j": "й",
    "k": "к",
    "l": "л",
    "m": "м",
    "n": "н",
    "o": "о",
    "p": "п",
    "r": "р",
    "s": "с",
    "t": "т",
    "u": "у",
    "f": "ф",
    "h": "х",
    "y": "ъ",
    "x": "кс",
    "q": "к",
    "w": "в",
    "c": "к",

}


# ============================================================
# TAG: TRANSLITERATE LATIN
# ============================================================

def transliterate_latin(text):

    text = text.lower()

    # Първо обработваме комбинациите.
    # Те трябва да се обработят преди единичните букви.

    combinations = [
        ("sht", "щ"),
        ("sch", "щ"),
        ("zh", "ж"),
        ("ch", "ч"),
        ("sh", "ш"),
        ("yu", "ю"),
        ("ju", "ю"),
        ("ya", "я"),
        ("ja", "я"),
        ("ts", "ц"),
    ]

    for latin, cyrillic in combinations:

        text = text.replace(
            latin,
            cyrillic
        )


    result = []


    for char in text:

        if char in LATIN_TO_CYRILLIC:

            result.append(
                LATIN_TO_CYRILLIC[char]
            )

        else:

            result.append(char)


    return "".join(result)


# ============================================================
# TAG: NORMALIZE TEXT
# ============================================================

def normalize_text(text):

    if text is None:
        return ""


    text = str(text).lower().strip()


    # Латински / шльокавица към кирилица.

    text = transliterate_latin(
        text
    )


    # Разделители към интервал.

    text = re.sub(
        r"[_\-.\/\\]+",
        " ",
        text
    )


    # Премахване на други символи.

    text = re.sub(
        r"[^\w\s]",
        " ",
        text,
        flags=re.UNICODE
    )


    # Премахване на излишни интервали.

    text = re.sub(
        r"\s+",
        " ",
        text
    ).strip()


    return text


# ============================================================
# TAG: TOKENIZE
# ============================================================

def tokenize(text):

    normalized = normalize_text(
        text
    )

    if not normalized:
        return []

    return normalized.split()


# ============================================================
# TAG: WORD VARIANTS
# ============================================================

def generate_variants(word):

    variants = {
        word
    }


    # Прости форми за български думи.
    # Това не е пълен морфологичен анализ,
    # а лек механизъм за търсене.

    suffixes = [
        "ите",
        "ите",
        "ия",
        "и",
        "а",
        "я",
        "о",
        "е",
        "и",
        "ен",
        "на",
        "но",
        "ни",
        "на",
        "ски",
        "ска",
        "ско"
    ]


    for suffix in suffixes:

        if (
            len(word) > len(suffix) + 2
            and word.endswith(suffix)
        ):

            variants.add(
                word[:-len(suffix)]
            )


    return variants


# ============================================================
# TAG: FUZZY SIMILARITY
# ============================================================

def similarity(word_a, word_b):

    if not word_a or not word_b:
        return 0.0


    if word_a == word_b:
        return 1.0


    # Съвпадение в началото.

    if (
        len(word_a) >= 4
        and len(word_b) >= 4
        and (
            word_a.startswith(word_b)
            or word_b.startswith(word_a)
        )
    ):

        return 0.90


    return SequenceMatcher(
        None,
        word_a,
        word_b
    ).ratio()


# ============================================================
# TAG: WORD MATCH
# ============================================================

def match_word(
    search_word,
    target_words
):

    best_score = 0.0

    best_word = None

    match_type = None


    search_variants = generate_variants(
        search_word
    )


    for search_variant in search_variants:

        for target_word in target_words:

            if search_variant == target_word:

                return (
                    1.0,
                    target_word,
                    "EXACT"
                )


            current = similarity(
                search_variant,
                target_word
            )


            if current > best_score:

                best_score = current

                best_word = target_word


    # Прагове за fuzzy matching.

    if best_score >= 0.88:

        match_type = "FUZZY"


    elif best_score >= 0.72:

        match_type = "RELATED"


    else:

        return (
            0.0,
            None,
            None
        )


    return (
        best_score,
        best_word,
        match_type
    )


# ============================================================
# TAG: LOAD CONFIG
# ============================================================

def load_config():

    if not CONFIG_FILE.exists():

        return {}


    with CONFIG_FILE.open(
        "r",
        encoding="utf-8"
    ) as file:

        return json.load(file)


# ============================================================
# TAG: CATEGORIZE DATASET
# ============================================================

def categorize_dataset(
    dataset,
    config
):

    text = " ".join([
        normalize_text(
            dataset.get("name", "")
        ),
        normalize_text(
            dataset.get("description", "")
        ),
        normalize_text(
            dataset.get("category", "")
        )
    ])


    text_words = set(
        tokenize(text)
    )


    category_scores = {}


    for category, keywords in config.items():

        score = 0


        for keyword in keywords:

            keyword_normalized = normalize_text(
                keyword
            )


            keyword_words = tokenize(
                keyword_normalized
            )


            for keyword_word in keyword_words:

                if keyword_word in text_words:

                    score += 2

                else:

                    for text_word in text_words:

                        if similarity(
                            keyword_word,
                            text_word
                        ) >= 0.88:

                            score += 1

                            break


        if score > 0:

            category_scores[
                category
            ] = score


    if not category_scores:

        return ["OTHER"]


    # Позволяваме повече от една категория.

    highest_score = max(
        category_scores.values()
    )


    categories = []


    for category, score in category_scores.items():

        if score >= max(
            1,
            highest_score * 0.5
        ):

            categories.append(
                category
            )


    return categories


# ============================================================
# TAG: FIELD MATCH
# ============================================================

def analyze_field(
    search_words,
    field_value
):

    normalized = normalize_text(
        field_value
    )


    target_words = tokenize(
        normalized
    )


    if not target_words:

        return {
            "score": 0,
            "matches": []
        }


    matches = []


    total_similarity = 0.0


    for search_word in search_words:

        (
            similarity_score,
            matched_word,
            match_type
        ) = match_word(
            search_word,
            target_words
        )


        if similarity_score > 0:

            total_similarity += (
                similarity_score
            )


            matches.append({

                "query": search_word,

                "matched": matched_word,

                "type": match_type,

                "similarity": round(
                    similarity_score,
                    2
                )
            })


    if not matches:

        return {
            "score": 0,
            "matches": []
        }


    # Средна степен на съвпадение.

    average_similarity = (
        total_similarity /
        len(search_words)
    )


    matched_count = len(
        matches
    )


    coverage = (
        matched_count /
        len(search_words)
    )


    return {
        "score": average_similarity * coverage,
        "matches": matches
    }


# ============================================================
# TAG: CALCULATE SCORE
# ============================================================

def calculate_score(
    dataset,
    search_text
):

    search_words = tokenize(
        search_text
    )


    if not search_words:

        return {
            "score": 0,
            "matches": [],
            "best_field": None
        }


    field_results = []


    for field, weight in FIELD_WEIGHTS.items():

        field_value = dataset.get(
            field,
            ""
        )


        result = analyze_field(
            search_words,
            field_value
        )


        if result["score"] > 0:

            weighted_score = (
                result["score"] *
                weight
            )


            field_results.append({

                "field": field,

                "score": weighted_score,

                "matches": result["matches"]

            })


    if not field_results:

        return {
            "score": 0,
            "matches": [],
            "best_field": None
        }


    # Събираме приносите от различните полета.

    total_score = sum(
        item["score"]
        for item in field_results
    )


    # Ограничаваме резултата до 100.

    final_score = min(
        100,
        total_score
    )


    # Най-силното поле.

    best_field = max(
        field_results,
        key=lambda item: item["score"]
    )


    # Всички съвпадения.

    all_matches = []


    for field_result in field_results:

        for match in field_result["matches"]:

            all_matches.append({

                "field": field_result["field"],

                "query": match["query"],

                "matched": match["matched"],

                "type": match["type"],

                "similarity": match["similarity"]

            })


    return {

        "score": round(
            final_score,
            1
        ),

        "matches": all_matches,

        "best_field": best_field["field"]

    }


# ============================================================
# TAG: CONFIDENCE
# ============================================================

def determine_confidence(score):

    if score >= 80:

        return "HIGH"


    if score >= 50:

        return "MEDIUM"


    return "LOW"


# ============================================================
# TAG: PRIORITY
# ============================================================

def determine_priority(score):

    if score >= 80:

        return "HIGH"


    if score >= 50:

        return "MEDIUM"


    return "LOW"


# ============================================================
# TAG: BUILD EXPLANATION
# ============================================================

def build_explanation(
    matches
):

    if not matches:

        return "No strong match found."


    explanations = []


    for match in matches:

        explanations.append(

            f"{match['query']} "
            f"→ {match['matched']} "
            f"({match['type']}, "
            f"{match['field']})"

        )


    return "; ".join(
        explanations
    )


# ============================================================
# TAG: SEARCH DATASETS
# ============================================================

def search_datasets(
    datasets,
    search_text,
    config,
    min_score
):

    results = []


    for dataset in datasets:

        score_data = calculate_score(
            dataset,
            search_text
        )


        score = score_data["score"]


        if score < min_score:

            continue


        categories = categorize_dataset(
            dataset,
            config
        )


        confidence = determine_confidence(
            score
        )


        priority = determine_priority(
            score
        )


        result = {

            "dataset": dataset,

            "score": score,

            "confidence": confidence,

            "priority": priority,

            "categories": categories,

            "best_field": score_data[
                "best_field"
            ],

            "matches": score_data[
                "matches"
            ],

            "explanation": build_explanation(
                score_data["matches"]
            )

        }


        results.append(
            result
        )


    # TAG: SORT RESULTS

    results.sort(

        key=lambda item: (

            item["score"],

            item["dataset"].get(
                "row_count"
            ) or 0

        ),

        reverse=True
    )


    return results


# ============================================================
# TAG: FIND LATEST FILE
# ============================================================

def find_latest_file():

    if not RAW_DIR.exists():

        raise FileNotFoundError(
            f"RAW папката не съществува: {RAW_DIR}"
        )


    folders = [

        folder

        for folder in RAW_DIR.iterdir()

        if folder.is_dir()

    ]


    if not folders:

        raise FileNotFoundError(
            "Не са намерени папки с RAW данни."
        )


    latest_folder = max(

        folders,

        key=lambda folder:
        folder.stat().st_mtime

    )


    files = list(

        latest_folder.glob(
            "sofiaplan_datasets.json"
        )

    )


    if not files:

        raise FileNotFoundError(

            f"Не е намерен "
            f"sofiaplan_datasets.json "
            f"в {latest_folder}"

        )


    return files[0]


# ============================================================
# TAG: LOAD DATA
# ============================================================

def load_datasets(
    file_path
):

    with file_path.open(
        "r",
        encoding="utf-8"
    ) as file:

        return json.load(file)


# ============================================================
# TAG: DISPLAY RESULTS
# ============================================================

def display_results(
    results,
    search_text
):

    print()

    print(
        "========================================"
    )

    print(
        " DATASET SEARCH V2.2"
    )

    print(
        "========================================"
    )

    print()

    print(
        f"Search: {search_text}"
    )

    print(
        f"Results: {len(results)}"
    )

    print()


    if not results:

        print(
            "Няма намерени datasets."
        )

        return


    for number, result in enumerate(
        results,
        start=1
    ):

        dataset = result["dataset"]


        print(
            "----------------------------------------"
        )


        print(

            f"[{number}] "
            f"Score: {result['score']} "
            f"| Confidence: "
            f"{result['confidence']} "
            f"| Priority: "
            f"{result['priority']}"

        )


        print(

            f"Categories: "
            f"{', '.join(result['categories'])}"

        )


        print(

            f"Best field: "
            f"{result['best_field']}"

        )


        print(

            f"ID: "
            f"{dataset.get('id', '-')}"

        )


        print(

            f"Name: "
            f"{dataset.get('name', '-')}"

        )


        print(

            f"Category: "
            f"{dataset.get('category', '-')}"

        )


        print(

            f"Provider: "
            f"{dataset.get('provider', '-')}"

        )


        print(

            f"Date: "
            f"{dataset.get('relevant_at', '-')}"

        )


        print(

            f"Rows: "
            f"{dataset.get('row_count', '-')}"

        )


        print(

            f"Size: "
            f"{dataset.get('size', '-')}"

        )


        print(

            f"Description: "
            f"{dataset.get('description', '-')}"

        )


        print(

            f"Why: "
            f"{result['explanation']}"

        )


    print(
        "----------------------------------------"
    )


# ============================================================
# TAG: JSON OUTPUT
# ============================================================

def display_json(
    results,
    search_text
):

    output = {

        "query": search_text,

        "result_count": len(
            results
        ),

        "results": []

    }


    for result in results:

        dataset = result["dataset"]


        output["results"].append({

            "id": dataset.get("id"),

            "name": dataset.get(
                "name"
            ),

            "description": dataset.get(
                "description"
            ),

            "source_category": dataset.get(
                "category"
            ),

            "provider": dataset.get(
                "provider"
            ),

            "relevant_at": dataset.get(
                "relevant_at"
            ),

            "row_count": dataset.get(
                "row_count"
            ),

            "size": dataset.get(
                "size"
            ),

            "score": result["score"],

            "confidence": result[
                "confidence"
            ],

            "priority": result[
                "priority"
            ],

            "categories": result[
                "categories"
            ],

            "best_field": result[
                "best_field"
            ],

            "matches": result[
                "matches"
            ],

            "explanation": result[
                "explanation"
            ]

        })


    print(
        json.dumps(
            output,
            ensure_ascii=False,
            indent=2
        )
    )


# ============================================================
# TAG: ARGUMENT PARSER
# ============================================================

def parse_arguments():

    arguments = sys.argv[1:]


    if not arguments:

        print(
            "Използване:"
        )

        print(
            "python tools/dataset_search.py дума"
        )

        print(
            "python tools/dataset_search.py дума --limit 20"
        )

        print(
            "python tools/dataset_search.py дума --min-score 70"
        )

        print(
            "python tools/dataset_search.py дума --json"
        )

        return None


    search_words = []

    limit = DEFAULT_LIMIT

    min_score = DEFAULT_MIN_SCORE

    json_output = False


    index = 0


    while index < len(arguments):

        argument = arguments[index]


        if argument == "--json":

            json_output = True


        elif argument == "--limit":

            if index + 1 >= len(arguments):

                raise ValueError(
                    "След --limit трябва да има число."
                )


            limit = int(
                arguments[index + 1]
            )

            index += 1


        elif argument == "--min-score":

            if index + 1 >= len(arguments):

                raise ValueError(
                    "След --min-score трябва да има число."
                )


            min_score = float(
                arguments[index + 1]
            )

            index += 1


        else:

            search_words.append(
                argument
            )


        index += 1


    search_text = " ".join(
        search_words
    )


    return (
        search_text,
        limit,
        min_score,
        json_output
    )


# ============================================================
# TAG: MAIN
# ============================================================

def main():

    try:

        arguments = parse_arguments()


        if arguments is None:

            return


        (
            search_text,
            limit,
            min_score,
            json_output
        ) = arguments


        # TAG: FIND DATA

        data_file = find_latest_file()


        if not json_output:

            print(
                "[INFO] Using data file:"
            )

            print(
                data_file
            )


        # TAG: LOAD DATA

        datasets = load_datasets(
            data_file
        )


        # TAG: LOAD CONFIG

        config = load_config()


        # TAG: SEARCH

        results = search_datasets(

            datasets,

            search_text,

            config,

            min_score

        )


        # TAG: LIMIT RESULTS

        results = results[
            :limit
        ]


        # TAG: OUTPUT

        if json_output:

            display_json(
                results,
                search_text
            )

        else:

            display_results(
                results,
                search_text
            )


    except Exception as error:

        print(
            f"[ERROR] {error}"
        )


# ============================================================
# TAG: PROGRAM START
# ============================================================

if __name__ == "__main__":

    main()