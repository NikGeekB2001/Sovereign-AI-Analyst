# Руководство по установке Sovereign-AI-Analyst

## Обзор

Данный документ описывает требования и процесс установки системы Sovereign-AI-Analyst в air-gapped среде без доступа к внешним ресурсам.

## Системные требования

### Аппаратные требования

#### Минимальная конфигурация
- **CPU**: 8 ядер, 2.5 GHz
- **RAM**: 32 GB
- **GPU**: NVIDIA RTX 4090 (24GB VRAM) или эквивалент
- **Storage**: 1 TB SSD для данных, 500 GB для системы
- **Network**: 1 Gbps Ethernet

#### Рекомендуемая конфигурация
- **CPU**: 16 ядер, 3.0 GHz
- **RAM**: 64 GB
- **GPU**: 2x NVIDIA RTX 4090 (или A6000) для высокой нагрузки
- **Storage**: 2 TB NVMe SSD для данных, 1 TB для системы
- **Network**: 10 Gbps для кластерной установки

### Программные требования

#### Операционная система
- **Linux**: Ubuntu 22.04 LTS, CentOS 8+, RHEL 8+
- **Windows**: Windows Server 2022 (ограниченная поддержка)
- **Container runtime**: Docker 24+, containerd

#### Необходимые компоненты
- **Python**: 3.11.x (необходимо установить заранее)
- **CUDA**: 12.1+ (для GPU ускорения)
- **NVIDIA drivers**: Совместимые с CUDA версией
- **Git**: 2.30+ (для клонирования репозитория, если применимо)

## Подготовка air-gapped среды

### Офлайн установка зависимостей

#### Подготовка на машине с интернетом
1. Создайте директорию для оффлайн установки:
```bash
mkdir sovereign-ai-offline
cd sovereign-ai-offline
```

2. Скачайте все необходимые зависимости (в среде с интернетом):
```bash
# Создание виртуального окружения
python3.11 -m venv offline_env
source offline_env/bin/activate

# Установка зависимостей с сохранением в wheel
pip download -r requirements_offline.txt --dest ./wheels
```

3. Архивируйте зависимости:
```bash
tar -czf wheels.tar.gz wheels/
```

4. Перенесите архив на air-gapped машину через USB или другой безопасный способ

#### Установка на air-gapped машине
1. Распакуйте архив:
```bash
tar -xzf wheels.tar.gz
```

2. Создайте виртуальное окружение:
```bash
python3.11 -m venv sovereign_ai_env
source sovereign_ai_env/bin/activate
```

3. Установите зависимости из локальных wheel:
```bash
pip install --find-links ./wheels --no-index --no-deps -r requirements_offline.txt
```

### Подготовка контейнеров

#### Сборка Docker образов оффлайн
1. На машине с интернетом соберите и сохраните образы:
```bash
# Сборка основного образа
docker build -t sovereign-ai-analyst:latest .
docker save -o sovereign-ai-analyst.tar sovereign-ai-analyst:latest

# Сборка и сохранение зависимостей
docker pull qdrant/qdrant:latest
docker pull neo4j:5-community
docker pull ghcr.io/huggingface/text-embeddings-inference:cpu-1.2
docker save -o dependencies.tar qdrant/qdrant:latest neo4j:5-community
```

2. Перенесите образы на air-gapped машину и загрузите:
```bash
docker load -i sovereign-ai-analyst.tar
docker load -i dependencies.tar
```

## Установка компонентов

### 1. Установка Python зависимостей

Создайте файл `requirements_offline.txt`:
```
fastapi>=0.104.1
uvicorn>=0.24.0
langgraph>=0.1.0
langchain-core>=0.2.0
pydantic>=2.5.0
sqlalchemy>=2.0.0
psycopg2-binary>=2.9.0
neo4j-driver>=5.0.0
qdrant-client>=1.7.0
python-jose[cryptography]>=3.3.0
passlib[bcrypt]>=1.7.0
python-multipart>=0.0.6
python-dotenv>=1.0.0
pytest>=7.4.0
requests>=2.31.0
numpy>=1.24.0
pandas>=2.1.0
pyjwt>=2.8.0
redis>=5.0.0
openai>=1.3.0
sentence-transformers>=2.2.0
torch>=2.12.0
transformers>=4.45.0
Pillow>=10.0.0
tiktoken>=0.5.0
langfuse==2.85.0
opentelemetry-api==1.22.0
```

Установите зависимости:
```bash
pip install --find-links ./wheels --no-index --no-deps -r requirements_offline.txt
```

### 2. Установка и настройка Qdrant

#### Опции установки:
**Вариант A: Через Docker (рекомендуется)**
```bash
docker run -d --name qdrant-server -p 6333:6333 -v $(pwd)/qdrant_data:/qdrant/storage qdrant/qdrant:latest
```

**Вариант B: Бинарная установка**
```bash
wget https://github.com/qdrant/qdrant/releases/download/v1.8.0/qdrant-x86_64-unknown-linux-gnu.tar.gz
tar -xzf qdrant-x86_64-unknown-linux-gnu.tar.gz
./qdrant &
```

#### Конфигурация:
Создайте `qdrant_config.yaml`:
```yaml
service:
  host: 0.0.0.0
  port: 6333
  grpc_port: 6334
  enable_cors: true
  enable_auth: false  # В air-gapped среде может быть отключен

storage:
  storage_path: "./storage"
  snapshots_path: "./snapshots"
```

### 3. Установка и настройка Neo4j

#### Опции установки:
**Через Docker (рекомендуется)**
```bash
docker run -d --name neo4j-container \
  -p 7687:7687 -p 7474:7474 \
  -v $(pwd)/neo4j_data:/data \
  -v $(pwd)/neo4j_logs:/logs \
  -v $(pwd)/neo4j_import:/var/lib/neo4j/import \
  -v $(pwd)/neo4j_plugins:/plugins \
  --env NEO4J_AUTH=none \
  --env NEO4J_dbms_security_auth_enabled=false \
  neo4j:5-community
```

#### Конфигурация:
Создайте `neo4j.conf`:
```
dbms.connector.bolt.listen_address=:7687
dbms.connector.http.listen_address=:7474
dbms.security.auth_enabled=false
dbms.memory.heap.initial_size=8G
dbms.memory.heap.max_size=16G
dbms.memory.pagecache.size=8G
```

### 4. Подготовка LLM моделей

#### Квантование моделей (для air-gapped среды)
Для работы в air-gapped среде рекомендуется использовать предварительно скаченные и квантованные модели:

1. Скачайте модель Qwen 2.5 7B (в среде с интернетом):
```bash
# Используйте huggingface-hub-cli или другие средства для скачивания
huggingface-cli download Qwen/Qwen2.5-7B-Instruct --local-dir qwen2.5-7b-instruct
```

2. Квантование модели (например, с помощью AutoAWQ):
```bash
# Этот процесс должен быть выполнен заранее в среде с интернетом
python -c "
from awq import AutoAWQForCausalLM
from transformers import AutoTokenizer

model_path = 'qwen2.5-7b-instruct'
quant_path = 'qwen2.5-7b-instruct-awq'

model = AutoAWQForCausalLM.from_pretrained(model_path, trust_remote_code=True)
tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)

model.quantize(tokenizer, quant_config={'zero_point': True, 'q_group_size': 128, 'w_bit': 4, 'version': 'GEMM'})

model.save_quantized(quant_path)
tokenizer.save_pretrained(quant_path)
"
```

3. Перенесите квантованную модель на air-gapped машину

### 5. Установка основного приложения

1. Скопируйте исходный код на air-gapped машину
2. Установите зависимости (как описано выше)
3. Настройте конфигурацию в `.env` файле:

```
# Qdrant Configuration
QDRANT_HOST=localhost
QDRANT_PORT=6333
QDRANT_COLLECTION_NAME=documents

# Neo4j Configuration
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password  # если аутентификация включена

# Model Configuration
MODEL_PATH=/path/to/qwen2.5-7b-instruct-awq
MODEL_MAX_LENGTH=8192

# Security Configuration
SECRET_KEY=your-secret-key-here
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30

# Application Configuration
APP_HOST=0.0.0.0
APP_PORT=8000
DEBUG_MODE=False
```

## Запуск системы

### 1. Запуск зависимостей
```bash
# Запуск Qdrant
docker start qdrant-server

# Запуск Neo4j
docker start neo4j-container

# Проверка доступности
curl http://localhost:6333/health
curl -u neo4j:your_password http://localhost:7474/db/neo4j/tx/commit
```

### 2. Запуск основного приложения
```bash
cd /path/to/sovereign-ai-analyst/src
source ../venv/bin/activate
python main.py
```

### 3. Альтернативно: запуск через Docker
```bash
docker run -d --name sovereign-ai-app \
  -p 8000:8000 \
  --gpus all \
  --env-file .env \
  --network container:qdrant-server \
  sovereign-ai-analyst:latest
```

## Проверка установки

### Проверка компонентов:
1. **API доступность**:
```bash
curl http://localhost:8000/
```

2. **Статус баз данных**:
```bash
# Qdrant
curl http://localhost:6333/collections

# Neo4j (если аутентификация включена)
curl -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -u neo4j:your_password \
  -X POST \
  -d '{"statements":[{"statement":"RETURN 1"}]}' \
  http://localhost:7474/db/neo4j/tx/commit
```

3. **Тестовый запрос**:
```bash
curl -X POST "http://localhost:8000/query" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your_jwt_token" \
  -d '{"query": "Тестовый запрос"}'
```

## Устранение неполадок

### Частые проблемы:

1. **GPU не обнаружена**:
   - Проверьте установку NVIDIA драйверов
   - Убедитесь, что CUDA версия совместима
   - Проверьте доступность GPU в Docker: `nvidia-smi`

2. **Память GPU недостаточна**:
   - Используйте квантованные модели (AWQ/GGUF)
   - Увеличьте VRAM или используйте CPU инференс

3. **Ошибки подключения к базам данных**:
   - Проверьте запущены ли контейнеры
   - Проверьте сетевые настройки
   - Убедитесь в правильности конфигурации в .env

4. **Ошибки аутентификации**:
   - Проверьте JWT токены
   - Убедитесь в правильности SECRET_KEY

## Безопасность установки

### Рекомендации:
- Используйте отдельную сеть для компонентов
- Настройте firewall для ограничения доступа
- Регулярно обновляйте компоненты из безопасных источников
- Используйте TLS для production развертываний
- Проведите security audit перед production запуском

## Заключение

Установка системы Sovereign-AI-Analyst в air-gapped среде требует тщательной подготовки и соблюдения всех шагов. Следуя этому руководству, вы сможете успешно развернуть безопасную и эффективную систему анализа корпоративных знаний.