#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# file: parse_study_plan.py

import re
import json
import pathlib
from typing import List, Dict, Optional

import pdfplumber

DATA = pathlib.Path("data")
PLAN_INDEX = DATA / "plan_files.json"
OUT_JSON   = DATA / "courses.json"

# ---------------------------
# Регексы и вспомогательные
# ---------------------------

RE_SEMESTER_CTX = re.compile(r"\b(\d+)\s*семестр", re.I)
RE_POOL_ELECT   = re.compile(r"Пул\s+выборн", re.I)
RE_REQUIRED     = re.compile(r"Обязательн", re.I)

# Строчка курса обычно заканчивается "ECTS HOURS", например: "3 108"
# Перед этим часто идёт порядковый номер, который игнорируем.
RE_COURSE_LINE  = re.compile(
    r"""^\s*
        (?:(\d+)\s+)?                # optional index
        (?P<name>.+?)\s+
        (?P<ects>\d{1,2})\s+
        (?P<hours>\d{2,4})\s*$
    """,
    re.X
)

# Иногда названия переносятся. Будем буферить строки,
# пока не получим хвост "ECTS HOURS".
def flush_buffer(buf: List[str]) -> Optional[Dict]:
    """
    Пытается распарсить накопленный буфер одной строкой.
    Возвращает {name, ects, hours} либо None.
    """
    if not buf:
        return None
    s = " ".join(x.strip() for x in buf if x.strip())
    s = re.sub(r"\s+", " ", s)
    m = RE_COURSE_LINE.match(s)
    if not m:
        return None
    name  = m.group("name").strip(" .;—-")
    ects  = int(m.group("ects"))
    hours = int(m.group("hours"))
    return {"name": name, "ects": ects, "hours": hours}

def clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()

# ---------------------------
# Основной разбор PDF
# ---------------------------

def parse_pdf_plan(pdf_path: pathlib.Path, program: str) -> List[Dict]:
    """
    Возвращает список словарей:
    { program, semester, type, name, ects, hours }
    """
    results: List[Dict] = []

    current_semester: Optional[int] = None
    current_type: str = "Не определено"  # "Обязательная" / "Выборная" / др.
    buf: List[str] = []

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            # 1) Сначала пытаемся забрать текст как есть (по строкам)
            text = page.extract_text() or ""
            lines = [clean(x) for x in text.splitlines() if clean(x)]

            # 2) Обновляем контекст: семестр/тип блока
            for i, line in enumerate(lines):
                # Смена типа блока
                if RE_POOL_ELECT.search(line):
                    current_type = "Выборная"
                    # Заодно вытащим семестр, если он упомянут в этой строке
                    m = RE_SEMESTER_CTX.search(line)
                    if m:
                        current_semester = int(m.group(1))
                elif RE_REQUIRED.search(line):
                    # Многие планы внутри "Обязательные дисциплины. 1 семестр ..."
                    current_type = "Обязательная"
                    m = RE_SEMESTER_CTX.search(line)
                    if m:
                        current_semester = int(m.group(1))

                # Явная смена семестра в любых строках
                m2 = RE_SEMESTER_CTX.search(line)
                if m2:
                    current_semester = int(m2.group(1))

                # 3) Детект курса: хвост "ECTS HOURS" на строке.
                #    С учётом переносов — накапливаем в буфер и пытаемся "сбросить".
                #    Если строка заканчивается "цифры цифры", это сильный признак.
                if re.search(r"\d+\s+\d+$", line):
                    buf.append(line)
                    item = flush_buffer(buf)
                    if item:
                        results.append({
                            "program": program,
                            "semester": current_semester,
                            "type": current_type,
                            **item
                        })
                        buf = []
                    else:
                        # не получилось — сбрасываем, но без добавления
                        buf = []
                else:
                    # Возможно, это кусок названия с переносом — копим
                    # Но отбрасываем очевидный шум (крупные заголовки/итоги блоков)
                    if not any(kw in line.lower() for kw in [
                        "блок 1", "блок 2", "блок 3", "блок 4",
                        "модули", "дисциплины", "практика", "аттестация",
                        "универсальная (надпрофессиональная) подготовка",
                        "магистратура/аспирантура", "мировоззренческий модуль",
                        "иностранный язык", "soft skills", "государственная итоговая аттестация"
                    ]):
                        buf.append(line)

            # На границе страницы пробуем тоже сбросить буфер (иногда курс кончается на следующей)
            item = flush_buffer(buf)
            if item:
                results.append({
                    "program": program,
                    "semester": current_semester,
                    "type": current_type,
                    **item
                })
                buf = []

            # 4) Дополнительная попытка — таблицы.
            #    Некоторые страницы имеют настоящие таблицы; используем edge/lines стратегию.
            try:
                tables = page.extract_tables({
                    "vertical_strategy":   "lines",
                    "horizontal_strategy": "lines",
                    "intersection_tolerance": 5,
                }) or []
            except Exception:
                tables = []

            for tbl in tables:
                for row in tbl:
                    if not row: 
                        continue
                    cells = [clean(c) for c in row if clean(c)]
                    if len(cells) < 3:
                        continue
                    # Ищем паттерн "... name ... ects hours"
                    # Берём последние две ячейки как числа, остальное — название.
                    try:
                        ects  = int(cells[-2])
                        hours = int(cells[-1])
                        name  = " ".join(cells[:-2])
                        # Чистим потенциальный индекс в начале
                        name  = re.sub(r"^\d+\s+", "", name).strip(" .;—-")
                        if name and ects > 0 and hours > 0:
                            results.append({
                                "program": program,
                                "semester": current_semester,
                                "type": current_type,
                                "name": name,
                                "ects": ects,
                                "hours": hours
                            })
                    except Exception:
                        continue

    # Пост-обработка: уберём явный мусор (короткие/служебные строки)
    clean_res = []
    for r in results:
        if not r["name"] or len(r["name"]) < 3:
            continue
        # отфильтровать общие заголовки, случайно попавшие
        low = r["name"].lower()
        if any(kw in low for kw in ["учебный план", "блок ", "семестр старт", "лист1"]):
            continue
        clean_res.append(r)

    return clean_res


# ---------------------------
# Основной сценарий
# ---------------------------

def main():
    if not PLAN_INDEX.exists():
        raise SystemExit("Нет data/plan_files.json. Сначала запусти загрузку PDF.")

    idx = json.loads(PLAN_INDEX.read_text("utf-8"))
    all_rows: List[Dict] = []

    for item in idx:
        program = item["program"]
        filep   = pathlib.Path(item["file"])
        if not filep.exists():
            print(f"[WARN] нет файла: {filep}")
            continue
        print(f"[INFO] parse {program}: {filep.name}")
        rows = parse_pdf_plan(filep, program)
        all_rows.extend(rows)
        print(f"[OK] {program}: извлечено {len(rows)} записей")

    OUT_JSON.write_text(json.dumps(all_rows, ensure_ascii=False, indent=2), "utf-8")
    print(f"\nSaved -> {OUT_JSON}  (всего {len(all_rows)} курсов)")

if __name__ == "__main__":
    main()