# Документация проекта Sovereign-AI-Analyst

В этой директории содержится вся проектная документация для платформы Sovereign-AI-Analyst. Ниже приведена структура документации и описание каждого раздела:

## Структура документации

### 0. Новые документы (2026-08, по итогам E2E-верификации)

- **mcp_design.md** - Контракт-дизайн API/MCP-поверхности: каталог инструментов с JSON Schema, модель ошибок SOV-*, scopes/RBAC, SSE-контракт, наблюдаемость, версии, план внедрения (contract-first, как проектируют API)
- **audit_2026-08-16.md** - Аудит репозитория и плана v2 по результатам живого E2E-прогона: доказанная работоспособность, 9 исправленных багов, риски, скорректированный roadmap

### 1. Архитектурные решения (ADR/) 
Содержит архитектурные решения проекта, оформленные в формате Architectural Decision Records (ADR):

- **001-technology-stack.md** - Архитектурное решение о технологическом стеке
- **002-security-by-design.md** - Архитектурное решение о безопасности
- **003-llm-serving-engine.md** - Выбор движка для обслуживания LLM
- **004-llm-model-selection.md** - Выбор модели LLM
- **005-vector-database.md** - Выбор векторной базы данных
- **006-orchestration-langgraph.md** - Оркестрация с использованием LangGraph
- **007-observability-stack.md** - Стек наблюдаемости
- **008-datasets-graphrag.md** - Использование датасетов для GraphRAG

### 2. Диаграммы C4 Model (C4_Model/)
Содержит диаграммы архитектуры в соответствии с методологией C4 Model:

- **Deployment_Diagram.puml** - Диаграмма развертывания
- **ER_Data_Model.puml** - ER модель данных
- **Level_1_Context.puml** - Контекстная диаграмма уровня 1
- **Level_2_Container.puml** - Диаграмма контейнеров уровня 2
- **Level_3_Component_Agent.puml** - Компонентная диаграмма агента уровня 3
- **Sequence_Data_Flow.puml** - Диаграмма последовательности потока данных
- **образец.puml** - Пример PlantUML диаграммы

### 3. Дополнительные материалы (Materials/)
Содержит вспомогательные материалы проекта:

- **ARCHITECTURE_PATTERNS.md** - Архитектурные паттерны
- **IMPLEMENTATION_PLAN.md** - План реализации
- **INSTALL_REQUIREMENTS.md** - Требования к установке
- **LLM_Comparison_Table.csv** - Таблица сравнения LLM
- **README_AGENT_SYSTEM.md** - Документация системы агента
- **ai_risk_matrix.md** - Матрица рисков ИИ
- **architecture_explanation.md** - Объяснение архитектуры
- **architecture_presentation.md** - Презентация архитектуры
- **architecture_summary.md** - Резюме архитектуры
- **finops_report.md** - Отчет FinOps
- **iac_cicd_mlops_pipeline.md** - Pipeline IaC, CI/CD, MLOps
- **model_card.md** - Карточка модели
- **project_roadmap.md** - Дорожная карта проекта
- **release_strategy.md** - Стратегия релизов
- **resource_calculations.csv** - Расчет ресурсов
- **security_architecture.md** - Архитектура безопасности
- **storage_strategy.md** - Стратегия хранения

## Исключения из репозитория

Следующие файлы намеренно исключены из репозитория (указаны в .gitignore):

- **multi_agent_colab.py** - Временный скрипт для Colab
- **multi_agent_prototype.py** - Прототип многопользовательской системы
- **architecture_visualization.html** - HTML-визуализация архитектуры

## Цель документации

Документация проекта призвана обеспечить полное понимание архитектуры, принятых решений и реализации платформы Sovereign-AI-Analyst, которая представляет собой автономную (air-gapped) систему анализа знаний для корпоративного использования без зависимости от внешних API.