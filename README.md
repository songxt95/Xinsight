# XiaoHou Insight - 学情分析助手 Pro

XiaoHou Insight 是一个基于 Flask 开发的学情分析辅助工具，主要用于帮助教师快速获取和分析学生的学习进度、班级课表以及生成相关报表。

## ✨ 核心功能

* **多用户登录与隔离**：基于 Session 实现不同教师用户的登录状态维护与数据隔离。
* **学情看板展示**：支持展示单个讲次的详细学情数据，并支持多维度查看课堂、课后及考试数据。
* **班级与课表获取**：自动拉取老师开课/结课及自学的班级列表，提供详细的课表安排。
* **并发加速处理**：使用 `ThreadPoolExecutor` 对班级名单和学生历史报告数据进行并发拉取，大幅提升加载效率。
* **一键数据导出**：
    * 支持将特定讲次的学情数据导出为 CSV 文件。
    * 内置 Playwright 无头浏览器环境，支持将数据报表直接渲染并导出为 PNG 长截图。
    * 导出图片时自动复制到系统剪贴板（需 HTTPS 安全上下文，见下方说明）。

## 🛠️ 技术栈

* **后端**：Python, Flask
* **并发服务**：Waitress（HTTP）/ Cheroot（HTTPS，原生支持 SSL、跨平台）
* **数据请求**：Requests
* **截图渲染**：Playwright

## 🚀 安装与运行指南

### 1. 环境要求
请确保已安装 Python 3.8 或更高版本。

### 2. 安装依赖
克隆或下载本项目后，在项目根目录下运行以下命令以安装所需的 Python 包：
```bash
pip install -r requirements.txt

```

### 3. 安装 Playwright 浏览器

由于本项目依赖 Playwright 提供的 Chromium 无头浏览器进行图片导出功能，您需要执行以下命令安装浏览器内核：

```bash
playwright install chromium

```

### 4. 启动服务

直接运行主程序即可启动服务：

```bash
python app.py

```

服务采用**双端口并存**策略：

| 协议 | 端口 | 服务器 | 剪贴板 | 说明 |
| :--- | :--- | :--- | :--- | :--- |
| HTTP | `6927` | waitress | ❌ | 始终开启，零配置，稳定 |
| HTTPS | `6928` | cheroot | ✅ | 仅当根目录存在证书（`cert.pem`/`key.pem`）时开启 |

> 未生成证书时只启动 HTTP（6927）；生成证书后会**同时**启动 HTTP（6927）和 HTTPS（6928），老师可按需选用。

启动成功后，控制台会显示：

```text
========================================
   XiaoHou Insight - 学情分析助手 Pro
   [HTTP]  http://0.0.0.0:6927   (无剪贴板)
   [HTTPS] https://0.0.0.0:6928  (支持剪贴板)
========================================

```

- 普通使用：浏览器访问 `http://localhost:6927`
- 需要导出图片自动复制到剪贴板：访问 `https://<服务器IP>:6928`（见下方 HTTPS 说明）

## 🔒 启用 HTTPS（可选，用于剪贴板复制功能）

浏览器的剪贴板 API（`navigator.clipboard.write`）只在**安全上下文**（HTTPS 或 `localhost`）下可用。
如果老师通过**局域网 IP**（如 `http://192.168.1.69:6927`）访问，导出图片时无法自动复制到剪贴板。
生成证书后服务会在 `6928` 端口额外开启 HTTPS 入口，访问该入口即可恢复剪贴板功能（HTTP 6927 入口同时保留）。

### 1. 生成自签证书

证书绑定服务器的局域网 IP，可传参指定（默认 `10.25.214.11`）：

* **Windows**：
  ```bash
  generate_cert.bat 192.168.1.69
  ```
* **macOS / Linux**：
  ```bash
  bash generate_cert.sh 192.168.1.69
  ```

执行后会在项目根目录生成 `cert.pem` 和 `key.pem`（有效期 10 年）。

### 2. 启动服务（自动开启 HTTPS）

`app.py` 会自动检测：根目录存在 `cert.pem` + `key.pem` 时，在 HTTP（6927）之外**额外开启** HTTPS（6928，cheroot）。无需修改任何代码，直接：

```bash
python app.py
```

控制台会同时显示 `[HTTP]` 与 `[HTTPS]` 两个入口。

### 3. 老师首次访问

访问 `https://<服务器IP>:6928`，浏览器会提示"您的连接不是私密连接"（自签证书所致）：
点 **"高级" → "继续前往"** 即可，之后不再提示，导出图片即可自动复制到剪贴板。

> ⚠️ `cert.pem` / `key.pem` 含私钥，已加入 `.gitignore`，请勿提交到仓库。

## ⚙️ 环境配置

* **SECRET_KEY**：用于 Flask Session 的加密。在部署时可以通过环境变量 `SECRET_KEY` 指定，如未指定则系统启动时会自动生成一个 32 字节的随机 token。
* **PLAYWRIGHT_CHROMIUM_EXECUTABLE**：可选，指定截图所用浏览器的可执行文件路径。未指定时系统会按平台（Windows/macOS/Linux）自动探测 Chrome/Edge，仍找不到则回退到 Playwright 自带的 Chromium。

## 📂 核心文件说明

* `app.py`: Web 服务器的主入口，定义了路由、登录/退出逻辑、页面渲染、CSV 下载以及利用 Playwright 生成截图的 API；HTTP(6927/waitress) 始终开启，存在证书时额外并存 HTTPS(6928/cheroot)。
* `data_loader.py`: 核心数据处理模块，封装了所有与底层 API 通信的接口，负责处理用户鉴权、数据清洗、多线程并行拉取数据及请求异常兜底处理。
* `generate_cert.bat` / `generate_cert.sh`: 自签 HTTPS 证书生成脚本（分别用于 Windows 和 macOS/Linux）。