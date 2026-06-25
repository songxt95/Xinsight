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

直接运行主程序即可启动服务。项目使用了 `waitress` 支持高并发，服务默认在 `6927` 端口启动：

```bash
python app.py

```

启动成功后，您可以在控制台看到以下输出提示：

```text
========================================
   XiaoHou Insight - 学情分析助手 Pro
   Listening on: [http://0.0.0.0:6927](http://0.0.0.0:6927)
========================================

```

在浏览器中访问 `http://localhost:6927` 即可打开系统界面。

## 🔒 启用 HTTPS（可选，用于剪贴板复制功能）

浏览器的剪贴板 API（`navigator.clipboard.write`）只在**安全上下文**（HTTPS 或 `localhost`）下可用。
如果老师通过**局域网 IP**（如 `http://192.168.1.69:6927`）访问，导出图片时无法自动复制到剪贴板。
启用 HTTPS 后即可恢复该功能。

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

### 2. 启动服务（自动切换 HTTPS）

`app.py` 会自动检测：根目录存在 `cert.pem` + `key.pem` 时启用 **HTTPS（cheroot）**，否则保持 **HTTP（waitress）**。无需修改任何代码，直接：

```bash
python app.py
```

控制台会显示 `[HTTPS] 剪贴板功能已启用（cheroot + ssl）`。

### 3. 老师首次访问

访问 `https://<服务器IP>:6927`，浏览器会提示"您的连接不是私密连接"（自签证书所致）：
点 **"高级" → "继续前往"** 即可，之后不再提示，导出图片即可自动复制到剪贴板。

> ⚠️ `cert.pem` / `key.pem` 含私钥，已加入 `.gitignore`，请勿提交到仓库。

## ⚙️ 环境配置

* **SECRET_KEY**：用于 Flask Session 的加密。在部署时可以通过环境变量 `SECRET_KEY` 指定，如未指定则系统启动时会自动生成一个 32 字节的随机 token。
* **PLAYWRIGHT_CHROMIUM_EXECUTABLE**：可选，指定截图所用浏览器的可执行文件路径。未指定时系统会按平台（Windows/macOS/Linux）自动探测 Chrome/Edge，仍找不到则回退到 Playwright 自带的 Chromium。

## 📂 核心文件说明

* `app.py`: Web 服务器的主入口，定义了路由、登录/退出逻辑、页面渲染、CSV 下载以及利用 Playwright 生成截图的 API；并根据证书是否存在自动在 HTTP(waitress)/HTTPS(cheroot) 之间切换。
* `data_loader.py`: 核心数据处理模块，封装了所有与底层 API 通信的接口，负责处理用户鉴权、数据清洗、多线程并行拉取数据及请求异常兜底处理。
* `generate_cert.bat` / `generate_cert.sh`: 自签 HTTPS 证书生成脚本（分别用于 Windows 和 macOS/Linux）。