#!/usr/bin/env bash
# Xinsight HTTPS 自签证书生成器 (macOS / Linux)
set -e

# 默认绑定 IP，可通过参数覆盖: ./generate_cert.sh 192.168.1.69
IP="${1:-10.25.214.11}"

echo "========================================"
echo "  Xinsight HTTPS 自签证书生成器"
echo "  绑定 IP: ${IP}"
echo "========================================"

if [ -f cert.pem ] && [ -f key.pem ]; then
    read -r -p "cert.pem / key.pem 已存在，是否覆盖？(y/N): " ans
    case "$ans" in
        y|Y) ;;
        *) echo "已取消。"; exit 0 ;;
    esac
fi

if ! command -v openssl >/dev/null 2>&1; then
    echo "[错误] 未找到 openssl，请先安装 (macOS 自带; Linux: apt/yum install openssl)。"
    exit 1
fi

echo "[1/2] 生成私钥和证书..."
openssl req -x509 -newkey rsa:2048 \
    -keyout key.pem -out cert.pem \
    -days 3650 -nodes \
    -subj "/CN=${IP}" \
    -addext "subjectAltName=IP:${IP}"

echo ""
echo "[2/2] 证书生成成功！"
echo "  - cert.pem (证书) / key.pem (私钥)"
echo "  - 有效期: 10 年, 绑定 IP: ${IP}"
echo ""
echo "老师首次访问 https://${IP}:6927 时："
echo "  1. 浏览器提示\"连接不是私密连接\""
echo "  2. 点\"高级\" -> \"继续前往\""
echo "  3. 之后剪贴板功能自动可用"
