# PitchKiln-01 · 灶台值守看板

Django 5 + PostgreSQL：灶台瓦片看板 + 右侧抽屉探针时间线，无 Vue/React SPA。

## 技术栈

- Django 5、PostgreSQL
- Session 登录
- HTMX：局部刷新灶台网格与抽屉
- Docker Compose：`web` + `db`

## 端口与数据库

| 服务 | 端口 |
|------|------|
| Web  | **4710** |
| Postgres | **6110**（容器内 5432） |

数据库账号：`pitchkiln` / `pitchkiln` / 库名 `pitchkiln`

## 快速启动

```bash
cd PitchKiln/PitchKiln-01
docker compose up --build -d
```

浏览器打开：http://localhost:4710

演示账号：

- `admin` / `123456`（超级用户）
- `worker` / `123456`（普通用户）

容器启动时会自动：`migrate` → `seed_data` → `collectstatic` → `gunicorn`

## 本地开发（可选）

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
# 确保本机 Postgres 监听 6110，或先 docker compose up -d db
set POSTGRES_HOST=localhost
set POSTGRES_PORT=6110
python manage.py migrate
python manage.py seed_data
python manage.py runserver 0.0.0.0:4710
```

## 业务模型

1. **ResinLot（来脂批）**：`lotCode`、`originPlace`、`arrivalKg`、`receivedAt`
2. **FireHearth（灶台）**：`lane`、`tag`（唯一）、`resinGrade`、相位 `cold|charging|ramping|holding|drawing`
3. **CookRun（熬制值守）**：归属灶台与来脂批、`openedAt`、`closedAt`（可空）、`targetSoftPointC`
4. **SoftPointProbe（软化点探针）**：归属值守、`sampledAt`、`softPointC`、`samplerName`
5. **NightDutyCard（夜班在岗卡）**：`lane`、`dutyDate`、`shiftName`、`maxOnDuty`、`supervisorName`；`lane + dutyDate + shiftName` 三者唯一

**业务规则一（出胶）**：将灶台相位切到 `drawing`（出胶）时，进行中的 CookRun 必须至少有一条 SoftPointProbe 的 `softPointC ≤ 95`。逻辑在 `apps/kiln/services/floor_rules.py`，由相位切换入口调用。

**业务规则二（夜班在岗卡）**：限制同一自然日、同一过道里同时处于升温或保温的灶台数。

- **如何计数**：实时在岗数 = 该过道当前相位为 `ramping`（升温）或 `holding`（保温）的灶台数；`drawing`（出胶）、`cold`（冷灶）、`charging`（装料）一律不计入。计数函数 `on_duty_count()` 与改相位入口同在 `apps/kiln/services/floor_rules.py`，看板过道旁的「在岗」也用它，同源同数。
- **何时拦截**：`装料→升温`、`升温→保温` 两步改相位前，先数该过道当前在岗数；当日该过道**没有卡**、或**已达当日最新卡上限**（在岗数 ≥ `maxOnDuty`），则拒绝并提示先建卡。其余相位进出不拦截。
- **最新卡**：同一过道同一值班日可有多张卡（按班次区分），校验时取最新一张（id 最大）；改上限（同过道+值班日+班次再提交即改卡）后，下一笔改相位立即吃新上限——每次校验都现查现数，不缓存。

## 界面

- 首页：**灶台值守看板** — 左侧班次条 + 按过道排布的灶台瓦片；过道分组旁显示当日卡上限与实时在岗数；点瓦片打开右侧抽屉（值守、探针时间线、改相位 / 登记探针 / 开灶）
- 次页：**来脂批** — 卡片时间线，非宽表 CRUD
- 次页：**夜班在岗**（班次条「夜」）— 建卡 / 改上限，今日卡实时在岗数与历史卡

## 种子数据

```bash
python manage.py seed_data
```

幂等：已有灶台则只保证账号与当日在岗卡存在。样例地名仅用「松脂坳 / 桐油坑」系。

种子后一过道有一张今日夜班卡（上限 1），且该过道已有一灶升温（在岗 1/1，正好顶格）：
此时对一过道任何灶做「升温→保温」都会被拦，改卡上限到 2 后下一笔立即放行；
三过道无卡，对「坳火-夜班」做「装料→升温」会被提示先建卡。

## 目录结构

```
PitchKiln-01/
  manage.py
  requirements.txt
  Dockerfile
  entrypoint.sh
  docker-compose.yml
  config/
  apps/kiln/          # 模型、视图、floor_rules、种子
  templates/floor/    # 值守看板 + 抽屉
  templates/resin/    # 来脂批时间线
  static/css/         # 值守台 ops-console 样式
```
