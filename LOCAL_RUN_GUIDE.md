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
cd /home/xxc/projects/RMMT-jaredenv1/RMMT-API
source venv/bin/activate
export DB_HOST=172.18.0.2 DB_PORT=3306 DB_NAME=roommate DB_USER=root DB_PASSWORD=41567dcd40f0658387200cf9ea23cf4d JWT_SECRET=dev-secret-123
python app.py
```

地址：`http://127.0.0.1:5001`

---

## 3) 启动前端

学生端（5173）：

```bash
cd /home/xxc/projects/RMMT-jaredenv1/RMMT-Student
export NVM_DIR="$HOME/.nvm"
source "$NVM_DIR/nvm.sh"
nvm use 20.19.0
npm run dev -- --host 0.0.0.0 --port 5173
```

管理端（5174）：

```bash
cd /home/xxc/projects/RMMT-jaredenv1/RMMT-Admin
export NVM_DIR="$HOME/.nvm"
source "$NVM_DIR/nvm.sh"
nvm use 20.19.0
npm run dev -- --host 0.0.0.0 --port 5174
```

---

## 4) 导入 test-data（推荐每次联调前执行）

```bash
cd /home/xxc/projects/RMMT-jaredenv1/RMMT-API
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
- 账号密码表自动写入：`/home/xxc/projects/RMMT-jaredenv1/RMMT-API/test-data/accounts.csv`

默认登录密码：
- 管理员：`admin@example.com / Admin123456`
- 学生：见 `RMMT-API/test-data/accounts.csv`（默认密码 `Student123`）

---

## 5) 启动匹配分数计算（手动执行一次）

```bash
cd /home/xxc/projects/RMMT-jaredenv1/RMMT-API
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
- 登录 `Network Error`：通常是 API 连接 DB 失败（`DB_HOST`/`DB_PASSWORD` 不对）。
- 命令粘在一行执行失败：按文档分行执行，或用 `&&` 连接。
