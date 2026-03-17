# Roommate Matcher

<p align="center">
  <img src="https://zhixin-college.github.io/RMMT-Doc/logo.svg" alt="Roommate Matcher Logo">
</p>

**Roommate Matcher** is a questionnaire-based roommate recommendation system developed for freshmen. This system aims to facilitate the process of finding compatible roommates through a detailed questionnaire analysis. Learn more on our [project homepage](https://xavier-xuan.github.io/RMMT-Doc/).

## Repositories

This project is divided into three main components, each housed in its own repository:

- **[RMMT-API](https://github.com/ZhiXin-College/RMMT-API)**: The backend API, handling data processing, user authentication, and server-side logic.

- **[RMMT-Student](https://github.com/ZhiXin-College/RMMT-Student)**: The frontend interface for students, providing a user-friendly environment to fill out questionnaires and view matches.

- **[RMMT-Admin](https://github.com/ZhiXin-College/RMMT-Admin)**: The administrative interface for system administrators to manage users, view statistics, and configure system settings.

## 本地运行 (Local Development)

需要 **Python 3** 和 **MySQL**。

```bash
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
export DB_HOST=127.0.0.1
export DB_PORT=3306
export DB_NAME=roommate
export DB_USER=root
export DB_PASSWORD=你的密码

```

数据库需先创建（如 `CREATE DATABASE roommate;`），表结构可用 `sql/` 目录下的脚本初始化。

## Contributing

Contributions are welcome! For major changes, please open an issue first to discuss what you would like to change. Please ensure to update tests as appropriate.

## License

This project is licensed under the GPL License - see the [LICENSE](LICENSE.md) file for details.
