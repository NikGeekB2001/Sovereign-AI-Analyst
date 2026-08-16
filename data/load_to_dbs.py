"""
Загрузка датасета RusLawOD в Neo4j и Qdrant.

Graph Schema (простая онтология):
  Узлы:
    - LegalAct: правовой акт (id, title, doc_type, doc_number, date, status, text)
    - Authority: орган власти (name)
    - Keyword: ключевое слово (value)
  Связи:
    - ISSUED_BY: LegalAct -> Authority
    - HAS_KEYWORD: LegalAct -> Keyword

Qdrant: эмбеддинги текстов актов для семантического поиска
"""

import os
import glob
import xml.etree.ElementTree as ET
from datetime import datetime

from neo4j import GraphDatabase
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
import requests

# ============ Конфигурация ============
DATA_DIR = os.path.join(os.path.dirname(__file__), "ruslawod", "corpus_xml_lite")
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
COLLECTION_NAME = "ruslawod"
BATCH_SIZE = 50  # для пакетной вставки
MAX_DOCS = int(os.getenv("MAX_DOCS", "500"))  # лимит документов


def parse_xml(filepath: str) -> dict | None:
    """Парсинг XML-файла RusLawOD."""
    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
        meta = root.find("meta")
        body = root.find("body")
        ident = meta.find("identification") if meta is not None else None

        if ident is None:
            return None

        # Извлекаем поля
        act_id = ident.findtext("pravogovruNd", default="").strip()
        if not act_id:
            # Попробуем атрибут val
            el = ident.find("pravogovruNd")
            act_id = el.get("val", "") if el is not None else ""

        def get_val(tag):
            el = ident.find(tag)
            if el is not None:
                return el.get("val", "") or el.text or ""
            return ""

        title = get_val("headingIPS") or ""
        doc_type = get_val("doc_typeIPS") or ""
        doc_number = get_val("docNumberIPS") or ""
        date_str = get_val("docdateIPS") or ""
        authority = get_val("doc_author_normal_formIPS") or ""
        issued_by = get_val("issuedByIPS") or ""
        signed_by = get_val("signedIPS") or ""
        status = get_val("statusIPS") or ""
        is_widely_used = get_val("is_widely_used") or "0"

        # Текст
        text_el = body.find("textIPS") if body is not None else None
        text = (text_el.text or "").strip() if text_el is not None else ""

        # Ключевые слова
        keywords = []
        kw_section = meta.find("keywords") if meta is not None else None
        if kw_section is not None:
            for kw in kw_section.findall("keywordByIPS"):
                val = kw.get("val", "") or kw.text or ""
                if val.strip():
                    keywords.append(val.strip())

        # Классификатор
        ref_section = meta.find("reference") if meta is not None else None
        if ref_section is None:
            ref_section = meta.find("references") if meta is not None else None
        classifier = ""
        if ref_section is not None:
            cl = ref_section.find("classifierByIPS")
            if cl is not None:
                classifier = cl.get("val", "") or cl.text or ""

        # Ссылки на другие акты (из текста)
        refs = []
        if text_el is not None:
            for ref in text_el.findall("ref"):
                ref_nd = ref.get("nd", "")
                if ref_nd:
                    refs.append(ref_nd)

        # Парсим дату
        parsed_date = None
        if date_str:
            try:
                parsed_date = datetime.strptime(date_str, "%d.%m.%Y").isoformat()[:10]
            except ValueError:
                parsed_date = date_str

        # Используем issuedByIPS если authority пустое
        if not authority and issued_by:
            authority = issued_by

        return {
            "act_id": act_id,
            "title": title[:500] if title else "",
            "doc_type": doc_type,
            "doc_number": doc_number,
            "date": parsed_date or "",
            "authority": authority,
            "signed_by": signed_by[:200] if signed_by else "",
            "status": status,
            "is_widely_used": is_widely_used == "1",
        "access_level": os.getenv("ACCESS_LEVEL_DEFAULT", "public")
        if is_widely_used == "1" else "internal",
            "text": text[:5000] if text else "",  # ограничиваем длину
            "keywords": keywords[:10],  # максимум 10 ключевых слов
            "classifier": classifier,
            "refs": refs[:20],  # максимум 20 ссылок
        }
    except Exception as e:
        print(f"  Ошибка парсинга {filepath}: {e}")
        return None


def get_embedding(text: str) -> list[float] | None:
    """Получить эмбеддинг через Ollama."""
    try:
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/embed",
            json={"model": EMBEDDING_MODEL, "input": text[:2000]},
            timeout=60,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("embeddings", [None])[0]
    except Exception as e:
        print(f"  Ошибка эмбеддинга: {e}")
    return None


def create_neo4j_schema(driver):
    """Создаём схему графа с индексами."""
    with driver.session() as session:
        # Уникальные ограничения (они же создают индексы)
        session.run(
            "CREATE CONSTRAINT legal_act_id IF NOT EXISTS "
            "FOR (a:LegalAct) REQUIRE a.act_id IS UNIQUE"
        )
        session.run(
            "CREATE CONSTRAINT authority_name IF NOT EXISTS "
            "FOR (a:Authority) REQUIRE a.name IS UNIQUE"
        )
        session.run(
            "CREATE CONSTRAINT keyword_value IF NOT EXISTS "
            "FOR (k:Keyword) REQUIRE k.value IS UNIQUE"
        )
        print("Neo4j: схема создана (3 типа узлов, уникальные ограничения)")


def insert_neo4j_batch(driver, docs: list[dict]):
    """Пакетная вставка в Neo4j."""
    with driver.session() as session:
        # Используем UNWIND для пакетной вставки
        session.run(
            """
            UNWIND $docs AS doc
            MERGE (a:LegalAct {act_id: doc.act_id})
            SET a.title = doc.title,
                a.doc_type = doc.doc_type,
                a.doc_number = doc.doc_number,
                a.date = doc.date,
                a.signed_by = doc.signed_by,
                a.status = doc.status,
                a.is_widely_used = doc.is_widely_used,
                a.access_level = doc.access_level,
                a.classifier = doc.classifier
            WITH a, doc
            WHERE doc.authority <> ''
            MERGE (auth:Authority {name: doc.authority})
            MERGE (a)-[:ISSUED_BY]->(auth)
            """,
            docs=docs,
        )

        # Ключевые слова — отдельным запросом
        kw_docs = []
        for doc in docs:
            for kw in doc["keywords"]:
                kw_docs.append({"act_id": doc["act_id"], "keyword": kw})

        if kw_docs:
            session.run(
                """
                UNWIND $items AS item
                MATCH (a:LegalAct {act_id: item.act_id})
                MERGE (k:Keyword {value: item.keyword})
                MERGE (a)-[:HAS_KEYWORD]->(k)
                """,
                items=kw_docs,
            )

        # Ссылки между актами
        ref_docs = []
        for doc in docs:
            for ref_id in doc["refs"]:
                ref_docs.append({"act_id": doc["act_id"], "ref_id": ref_id})

        if ref_docs:
            session.run(
                """
                UNWIND $items AS item
                MATCH (a:LegalAct {act_id: item.act_id})
                MERGE (b:LegalAct {act_id: item.ref_id})
                MERGE (a)-[:REFERENCES]->(b)
                """,
                items=ref_docs,
            )


def create_qdrant_collection(client: QdrantClient, vector_size: int = 768):
    """Создаём коллекцию в Qdrant."""
    collections = [c.name for c in client.get_collections().collections]
    if COLLECTION_NAME in collections:
        print(f"Qdrant: коллекция '{COLLECTION_NAME}' уже существует")
        return

    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
    )
    print(f"Qdrant: коллекция '{COLLECTION_NAME}' создана (размер вектора={vector_size})")


def insert_qdrant_batch(client: QdrantClient, docs: list[dict], embeddings: list[list[float]], start_id: int):
    """Пакетная вставка в Qdrant."""
    points = []
    for i, (doc, emb) in enumerate(zip(docs, embeddings)):
        if emb is None:
            continue
        points.append(
            PointStruct(
                id=start_id + i,
                vector=emb,
                payload={
                    "act_id": doc["act_id"],
                    "title": doc["title"],
                    "doc_type": doc["doc_type"],
                    "date": doc["date"],
                    "status": doc["status"],
                    "authority": doc["authority"],
                    "text_preview": doc["text"][:500],
                    # RBAC: public для широко используемых/действующих, иначе internal
                    "access_level": doc.get("access_level", "public"),
                },
            )
        )
    if points:
        client.upsert(collection_name=COLLECTION_NAME, points=points)
    return len(points)


def main():
    print("=" * 60)
    print("Загрузка RusLawOD в Neo4j + Qdrant")
    print("=" * 60)

    # 1. Находим XML-файлы
    xml_files = sorted(glob.glob(os.path.join(DATA_DIR, "*.xml")))
    if not xml_files:
        print(f"XML файлы не найдены в {DATA_DIR}")
        return
    print(f"Найдено XML файлов: {len(xml_files)}")
    print(f"Лимит документов: {MAX_DOCS}")
    xml_files = xml_files[:MAX_DOCS]

    # 2. Парсим XML
    print("\nПарсинг XML...")
    docs = []
    for f in xml_files:
        doc = parse_xml(f)
        if doc and doc["act_id"] and doc["title"]:
            docs.append(doc)
    print(f"Успешно распарсено: {len(docs)} документов")

    # Статистика
    doc_types = {}
    authorities = {}
    for d in docs:
        doc_types[d["doc_type"]] = doc_types.get(d["doc_type"], 0) + 1
        if d["authority"]:
            authorities[d["authority"]] = authorities.get(d["authority"], 0) + 1
    print(f"\nТипы документов: {dict(list(doc_types.items())[:10])}")
    print(f"Органы власти: {len(authorities)} уникальных")

    # 3. Подключение к Neo4j
    print("\n--- Neo4j ---")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver.verify_connectivity()
    print("Подключение к Neo4j: OK")

    create_neo4j_schema(driver)

    # Пакетная вставка в Neo4j
    print("Загрузка в Neo4j...")
    for i in range(0, len(docs), BATCH_SIZE):
        batch = docs[i : i + BATCH_SIZE]
        insert_neo4j_batch(driver, batch)
        print(f"  Neo4j: {min(i + BATCH_SIZE, len(docs))}/{len(docs)}")

    # Проверка
    with driver.session() as session:
        result = session.run("MATCH (a:LegalAct) RETURN count(a) AS cnt")
        act_count = result.single()["cnt"]
        result = session.run("MATCH (a:Authority) RETURN count(a) AS cnt")
        auth_count = result.single()["cnt"]
        result = session.run("MATCH (k:Keyword) RETURN count(k) AS cnt")
        kw_count = result.single()["cnt"]
        result = session.run("MATCH ()-[r:ISSUED_BY]->() RETURN count(r) AS cnt")
        ib_count = result.single()["cnt"]
        result = session.run("MATCH ()-[r:HAS_KEYWORD]->() RETURN count(r) AS cnt")
        hk_count = result.single()["cnt"]
        result = session.run("MATCH ()-[r:REFERENCES]->() RETURN count(r) AS cnt")
        ref_count = result.single()["cnt"]
    print(f"\nNeo4j итог: LegalAct={act_count}, Authority={auth_count}, Keyword={kw_count}")
    print(f"  Связи: ISSUED_BY={ib_count}, HAS_KEYWORD={hk_count}, REFERENCES={ref_count}")
    driver.close()

    # 4. Подключение к Qdrant
    print("\n--- Qdrant ---")
    qclient = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    print("Подключение к Qdrant: OK")

    # Определяем размер вектора через тестовый эмбеддинг
    print(f"Получение тестового эмбеддинга (модель: {EMBEDDING_MODEL})...")
    test_emb = get_embedding("тест")
    if test_emb is None:
        print("ОШИБКА: не удалось получить эмбеддинг от Ollama!")
        print(f"Убедитесь, что Ollama запущена и модель {EMBEDDING_MODEL} установлена:")
        print(f"  ollama pull {EMBEDDING_MODEL}")
        return
    vector_size = len(test_emb)
    print(f"Размер вектора: {vector_size}")

    create_qdrant_collection(qclient, vector_size)

    # Загрузка в Qdrant с эмбеддингами
    print("Генерация эмбеддингов и загрузка в Qdrant...")
    total_inserted = 0
    for i in range(0, len(docs), BATCH_SIZE):
        batch = docs[i : i + BATCH_SIZE]
        # Формируем тексты для эмбеддингов
        texts = []
        for d in batch:
            emb_text = f"{d['title']} {d['doc_type']} {d['authority']} {d['text'][:1000]}"
            texts.append(emb_text)

        # Получаем эмбеддинги
        embeddings = []
        for text in texts:
            emb = get_embedding(text)
            embeddings.append(emb)

        inserted = insert_qdrant_batch(qclient, batch, embeddings, start_id=i)
        total_inserted += inserted
        print(f"  Qdrant: {min(i + BATCH_SIZE, len(docs))}/{len(docs)} (вставлено: {total_inserted})")

    print(f"\nQdrant итог: {total_inserted} векторов в коллекции '{COLLECTION_NAME}'")

    # 5. Итого
    print("\n" + "=" * 60)
    print("ЗАГРУЗКА ЗАВЕРШЕНА!")
    print(f"  Neo4j: {act_count} LegalAct, {auth_count} Authority, {kw_count} Keyword")
    print(f"  Qdrant: {total_inserted} векторов")
    print("=" * 60)


if __name__ == "__main__":
    main()
