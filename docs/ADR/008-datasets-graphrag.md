# ADR-008: Выбор датасетов для GraphRAG (Российское законодательство)

## Статус
Принято

## Контекст
Необходимо выбрать датасеты для наполнения GraphRAG системы Sovereign-AI-Analyst. Требования:
- Российское законодательство (актуально для enterprise РФ)
- Поддержка русского языка
- Метаданные для RBAC (тип документа, орган, статус)
- Ссылки между документами (для Knowledge Graph)
- Open-source лицензии

## Решение
Выбраны два互补ных датасета:

### 1. RusLawOD — Корпус российских правовых актов
- **Источник:** https://github.com/irlcode/RusLawOD
- **Объем:** 304,864 XML документов (194M токенов)
- **Период:** 1991–2025
- **Формат:** XML (Akoma Ntoso совместимый)
- **Лицензия:** Открытая (тексты законов не охраняются авторским правом)

**Структура XML:**
```xml
<act>
  <meta>
    <identification>
      <pravogovruNd val="102010083"/>           <!-- ID документа -->
      <issuedByIPS val="Постановление ВЦИК"/>    <!-- Кто издал -->
      <doc_typeIPS val="Постановление"/>          <!-- Тип документа -->
      <doc_author_normal_formIPS val="совместный"/> <!-- Орган (норм.) -->
      <docdateIPS val="20.09.1937"/>             <!-- Дата подписания -->
      <docNumberIPS val="б/н"/>                  <!-- Номер -->
      <headingIPS val="Об изменении..."/>        <!-- Заголовок -->
      <statusIPS val="Действует без изменений"/>  <!-- Статус -->
      <is_widely_used val="1"/>                  <!-- Широко используемый -->
    </identification>
    <keywords>
      <keywordsByIPS/>                           <!-- Ключевые слова -->
    </keywords>
    <reference>
      <classifierByIPS/>                         <!-- Классификатор -->
    </reference>
  </meta>
  <body>
    <textIPS>Текст документа...</textIPS>        <!-- Текст закона -->
  </body>
</act>
```

**Метаданные для RBAC:**
| Поле XML | Маппинг RBAC | Описание |
|----------|-------------|----------|
| `doc_typeIPS` | `access_level` | Тип: ФЗ=3, Постановление=2, Приказ=1 |
| `doc_author_normal_formIPS` | `allowed_roles` | Орган: Президент=executive, Правительство=finance,all |
| `statusIPS` | `is_active` | Действует/Утратил силу |
| `is_widely_used` | `priority` | Приоритет индексации |

### 2. RFSD — Russian Financial Statements Dataset
- **Источник:** https://huggingface.co/datasets/irlspbru/RFSD
- **Объем:** Финансовая отчетность компаний РФ
- **Формат:** HuggingFace Dataset (parquet)
- **Лицензия:** CC BY 4.0

**Назначение:** Финансовые данные для аналитических запросов (отчеты, показатели, динамика).

## Graph Schema (Простая онтология — 3-4 типа узлов)

### Узлы (Nodes)
| Тип | Свойства | Описание |
|-----|----------|----------|
| `Document` | id, title, doc_type, date, status, access_level, allowed_roles | Правовой акт |
| `Entity` | id, name, type (Organization/Person/Concept) | Сущность из текста |
| `Keyword` | id, name, classifier_code | Ключевое слово/тема |

### Ребра (Relationships)
| Тип | From → To | Описание |
|-----|-----------|----------|
| `AMENDS` | Document → Document | Документ изменяет другой |
| `REFERS_TO` | Document → Document | Ссылка на другой документ |
| `ISSUED_BY` | Document → Entity | Документ издан органом |
| `ABOUT` | Document → Keyword | Тематика документа |
| `RELATED_TO` | Entity → Entity | Связь между сущностями |

### RBAC на узлах графа
```cypher
// Каждый узел имеет RBAC свойства
(:Document {
  access_level: 2,           // 1=public, 2=internal, 3=confidential
  allowed_roles: ["finance", "legal"],  // Кто может видеть
  doc_type: "Федеральный закон"
})

// Запрос с RBAC фильтрацией
MATCH (d:Document)-[:ABOUT]->(k:Keyword {name: "налоги"})
WHERE d.access_level <= $clearance_level
  AND ANY(role IN $user_roles WHERE role IN d.allowed_roles)
RETURN d
```

## Ingestion Pipeline

### Шаг 1: Парсинг XML → Чанки
```python
import xml.etree.ElementTree as ET

def parse_ruslawod_xml(xml_path: str) -> dict:
    tree = ET.parse(xml_path)
    root = tree.getroot()

    meta = root.find("meta/identification")
    body = root.find("body/textIPS")

    return {
        "doc_id": meta.findtext("pravogovruNd/@val", ""),
        "title": meta.findtext("headingIPS", ""),
        "doc_type": meta.findtext("doc_typeIPS/@val", ""),
        "author": meta.findtext("doc_author_normal_formIPS/@val", ""),
        "date": meta.findtext("docdateIPS/@val", ""),
        "status": meta.findtext("statusIPS/@val", ""),
        "text": body.text if body is not None else "",
        "access_level": map_doc_type_to_access(meta.findtext("doc_typeIPS/@val", "")),
        "allowed_roles": map_author_to_roles(meta.findtext("doc_author_normal_formIPS/@val", "")),
    }
```

### Шаг 2: Чанки → Qdrant (Vector DB)
```python
# Chunking с метаданными RBAC
chunks = text_splitter.split_text(doc["text"])
for i, chunk in enumerate(chunks):
    qdrant_client.upsert(
        collection_name="ruslawod",
        points=[{
            "id": f"{doc['doc_id']}_chunk_{i}",
            "vector": embed(chunk),
            "payload": {
                "text": chunk,
                "doc_id": doc["doc_id"],
                "doc_type": doc["doc_type"],
                "access_level": doc["access_level"],
                "allowed_roles": doc["allowed_roles"],
            }
        }]
    )
```

### Шаг 3: Сущности → Neo4j (Graph DB)
```cypher
// Создание узла документа
MERGE (d:Document {id: $doc_id})
SET d.title = $title, d.doc_type = $doc_type,
    d.access_level = $access_level, d.allowed_roles = $allowed_roles

// Создание узла сущности (орган)
MERGE (e:Entity {name: $author, type: "Organization"})
MERGE (d)-[:ISSUED_BY]->(e)

// Создание связей между документами (из <ref> тегов)
MERGE (d2:Document {id: $ref_doc_id})
MERGE (d)-[:REFERS_TO]->(d2)
```

## Capacity Planning

### RusLawOD
| Параметр | Значение |
|----------|----------|
| Документов | 304,864 |
| Средний размер | ~5KB текста |
| Чанков (512 токенов) | ~380K |
| Векторов (768-dim) | ~1.2GB |
| Узлов графа | ~400K (docs + entities) |
| Ребер графа | ~1.5M (refs + issued_by) |

### RFSD
| Параметр | Значение |
|----------|----------|
| Записей | TBD (после загрузки) |
| Чанков | TBD |
| Узлов графа | Компании + Показатели |

## Последствия

### Положительные
- ✅ 304K+ реальных российских правовых документов
- ✅ Метаданные для RBAC (тип, орган, статус)
- ✅ Ссылки между документами для Knowledge Graph
- ✅ Простая онтология (3 типа узлов) — легко масштабировать
- ✅ Финансовые данные (RFSD) для аналитических запросов

### Отрицательные
- ⚠️ Большой объем данных (~380K чанков, ~1.5M ребер)
- ⚠️ Не все документы имеют полные метаданные
- ⚠️ Требуется очистка текста от XML-разметки

### Нейтральные
- Онтологию можно расширять по мере необходимости
- RFSD дополняет RusLawOD финансовой аналитикой

## Альтернативы рассмотрены
1. **Только RusLawOD** — недостаточно для финансовых запросов
2. **КонсультантПлюс / Гарант** — проприетарные, нарушает air-gapped
3. **Pravo.gov.ru API** — неполные данные, нет текстового слоя
4. **Сложная онтология (10+ типов узлов)** — преждевременная оптимизация

## Ссылки
- [RusLawOD GitHub](https://github.com/irlcode/RusLawOD)
- [RusLawOD Paper (arXiv)](https://arxiv.org/abs/2406.04855)
- [RFSD HuggingFace](https://huggingface.co/datasets/irlspbru/RFSD)
- [Akoma Ntoso Standard](http://www.akomantoso.org/)
