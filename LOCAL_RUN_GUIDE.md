# 本地运行速查（RMMT-jaredenv1）

## 1) 启动 MySQL（docker）

```bash
docker ps --format '{{.Names}}\t{{.Status}}' | awk '/rmmt-mysql/' || docker start rmmt-mysql
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' rmmt-mysql
docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' rmmt-mysql
```

通俗解释：`端口映射`就是把“容器里的门”接到“你电脑上的门”。  
当前 MySQL 主要在容器网络里，API 要用容器 IP（如 `172.18.0.2`）连接。

---

## 2) 启动后端 API（5001）

```bash
cd /home/xxc/projects/RMMT-my-feature-deploy/RMMT-API
source venv/bin/activate
export DB_HOST=172.18.0.2 DB_PORT=3306 DB_NAME=roommate DB_USER=root DB_PASSWORD=41567dcd40f0658387200cf9ea23cf4d JWT_SECRET=dev-secret-123
python app.py
```

地址：`http://127.0.0.1:5001`（端口在 `app.py` 的 `app.run(..., port=5001)`，勿与前端 `.env` 写错成 5101 等）

---

## 3) 启动前端

学生端（5173）：

```bash
cd /home/xxc/projects/RMMT-my-feature-deploy/RMMT-Student
export NVM_DIR="$HOME/.nvm"
source "$NVM_DIR/nvm.sh"
nvm use 20.19.0
npm run dev -- --host 0.0.0.0 --port 5173
```

管理端（5174）：

```bash
cd /home/xxc/projects/RMMT-my-feature-deploy/RMMT-Admin
export NVM_DIR="$HOME/.nvm"
source "$NVM_DIR/nvm.sh"
nvm use 20.19.0
npm run dev -- --host 0.0.0.0 --port 5174
```

---

## 4) 导入 test-data（推荐每次联调前执行）

```bash
cd /home/xxc/projects/RMMT-my-feature-deploy/RMMT-API
source venv/bin/activate
python test-data/seed_test_data.py \
  --db-host 172.18.0.2 \
  --db-port 3306 \
  --db-user root \
  --db-password 41567dcd40f0658387200cf9ea23cf4d \
  --db-name roommate
```

导入结果：
- 若数据库无问卷：自动生成 3 个分页、9 题（每页 3 题）
- 1 个管理员 + 20 个学生（10 男 10 女）
- 学生资料、问卷答案、题目权重随机生成
- 每个性别：4 人满队、2 人半满队、4 人未组队
- 账号密码表自动写入：`/home/xxc/projects/RMMT-my-feature-deploy/RMMT-API/test-data/accounts.csv`

默认登录密码：
- 管理员：`admin@example.com / Admin123456`
- 学生：见 `RMMT-API/test-data/accounts.csv`（默认密码 `Student123`）

---

## 5) 启动匹配分数计算（手动执行一次）

```bash
cd /home/xxc/projects/RMMT-my-feature-deploy/RMMT-API
source venv/bin/activate
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 DB_HOST=172.18.0.2 DB_PORT=3306 DB_NAME=roommate DB_USER=root DB_PASSWORD=41567dcd40f0658387200cf9ea23cf4d JWT_SECRET=dev-secret-123
python -c "from tasks import scan_students; scan_students()"
```

通俗解释：
- `匹配分数计算`：根据学生问卷答案，计算两两匹配分并写入数据库。
- `HF_HUB_OFFLINE=1`：离线模式，只用本地模型，避免因外网不可用导致卡住。
- `手动执行一次`：该命令会立即跑完一轮，然后退出（不是一直循环后台跑）。

日志看到 `算法匹配完成` 就表示本轮成功。

---

## 6) 常见问题

- `Address already in use`：5001 已被占用，先结束旧 API 进程再启动。
- 登录 **`Network Error`**（浏览器里请求发不出去）：
  - **最常见**：学生端/管理端 `.env` 里 **`VITE_API_URL` 端口写错**（例如写成 `5101`，而后端是 **`5001`**）。  
    **建议**：本地开发**不要设置** `VITE_API_URL`，让请求走 **Vite 代理**（`vite.config.ts` 已把 **`/api`** 与 **`/static`** 转到 `http://127.0.0.1:5001`）。改完 `.env` 后需**重启** `npm run dev`。
  - 后端未启动或不在 5001：先确认终端里 Flask 已跑起来。
  - **部署生产**：构建前端时**必须**设置正确的 `VITE_API_URL`（与线上 API 同源或由网关反代 `/api`），并在服务器/Nginx 上把 `/api` 转到后端。
- API 能启动但接口返回 500 / 连不上库：检查 `DB_HOST`/`DB_PASSWORD` 等（与「Network Error」不同，后者多为连不上 HTTP 服务）。
- 命令粘在一行执行失败：按文档分行执行，或用 `&&` 连接。
