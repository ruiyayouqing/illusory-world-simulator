#!/bin/bash
# ============================================================
# 太虚幻境 云服务版 — 一键部署脚本
# 适用系统: Ubuntu 22.04 LTS
# 使用方法:
#   1. 将整个 taiwuyunjing-cloud 文件夹上传到服务器
#   2. cd taiwuyunjing-cloud
#   3. chmod +x deploy/deploy.sh
#   4. sudo ./deploy/deploy.sh
# ============================================================
set -e

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  太虚幻境 云服务版 部署脚本${NC}"
echo -e "${BLUE}========================================${NC}"

# 检查 root 权限
if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}请使用 root 权限运行此脚本（sudo ./deploy/deploy.sh）${NC}"
  exit 1
fi

# 当前目录（脚本所在目录的上层是项目根目录）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
INSTALL_DIR="/opt/taiwuyunjing"

echo -e "${YELLOW}项目源码目录: ${PROJECT_DIR}${NC}"
echo -e "${YELLOW}安装目录: ${INSTALL_DIR}${NC}"

# ===== 1. 安装系统依赖 =====
echo -e "\n${GREEN}[1/8] 安装系统依赖...${NC}"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip nginx sqlite3 > /dev/null 2>&1
echo -e "${GREEN}系统依赖安装完成${NC}"

# ===== 2. 创建安装目录 =====
echo -e "\n${GREEN}[2/8] 创建安装目录...${NC}"
mkdir -p "$INSTALL_DIR"
# 复制项目文件
rsync -a --exclude='__pycache__' --exclude='*.pyc' --exclude='.git' \
  --exclude='venv' --exclude='*.log' \
  "$PROJECT_DIR/" "$INSTALL_DIR/"
echo -e "${GREEN}文件复制完成${NC}"

cd "$INSTALL_DIR"

# ===== 3. 创建虚拟环境 =====
echo -e "\n${GREEN}[3/8] 创建 Python 虚拟环境...${NC}"
if [ ! -d "venv" ]; then
  python3 -m venv venv
fi
source venv/bin/activate
echo -e "${GREEN}虚拟环境就绪${NC}"

# ===== 4. 安装 Python 依赖 =====
echo -e "\n${GREEN}[4/8] 安装 Python 依赖（可能需要几分钟）...${NC}"
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo -e "${GREEN}Python 依赖安装完成${NC}"

# ===== 5. 创建必要目录 =====
echo -e "\n${GREEN}[5/8] 创建数据目录...${NC}"
mkdir -p data/worlds
mkdir -p data/user_data
mkdir -p saves
mkdir -p static
mkdir -p plugins
# 复制示例世界模板（如果存在）
if [ -f "data/worlds/demo_ming/world.json" ]; then
  echo -e "${GREEN}世界模板已存在${NC}"
else
  echo -e "${YELLOW}警告: 未找到示例世界模板 data/worlds/demo_ming/world.json${NC}"
  echo -e "${YELLOW}请从原项目复制 data 目录${NC}"
fi
# 创建 config.json（若不存在则从示例复制）
if [ ! -f "config.json" ]; then
  if [ -f "config.example.json" ]; then
    cp config.example.json config.json
    echo -e "${GREEN}已从 config.example.json 创建 config.json${NC}"
    echo -e "${YELLOW}请登录管理后台配置 API Key${NC}"
  else
    echo -e "${YELLOW}警告: 未找到 config.example.json，程序将使用默认空配置${NC}"
  fi
else
  echo -e "${GREEN}config.json 已存在，跳过${NC}"
fi
# 设置目录权限
chown -R www-data:www-data "$INSTALL_DIR"
echo -e "${GREEN}数据目录就绪${NC}"

# ===== 6. 配置 Nginx =====
echo -e "\n${GREEN}[6/8] 配置 Nginx 反向代理...${NC}"
cp deploy/nginx.conf /etc/nginx/sites-available/taiwuyunjing
ln -sf /etc/nginx/sites-available/taiwuyunjing /etc/nginx/sites-enabled/taiwuyunjing
# 移除默认站点（避免冲突）
if [ -f /etc/nginx/sites-enabled/default ]; then
  rm -f /etc/nginx/sites-enabled/default
  echo -e "${YELLOW}已移除 Nginx 默认站点${NC}"
fi
# 测试 Nginx 配置
if nginx -t 2>/dev/null; then
  systemctl restart nginx
  systemctl enable nginx
  echo -e "${GREEN}Nginx 配置完成${NC}"
else
  echo -e "${RED}Nginx 配置测试失败，请检查 /etc/nginx/sites-available/taiwuyunjing${NC}"
fi

# ===== 7. 配置 Systemd 服务 =====
echo -e "\n${GREEN}[7/8] 配置 Systemd 服务...${NC}"
cp deploy/taiwuyunjing.service /etc/systemd/system/taiwuyunjing.service
systemctl daemon-reload
systemctl enable taiwuyunjing
systemctl restart taiwuyunjing
echo -e "${GREEN}服务已启动并设置为开机自启${NC}"

# ===== 8. 防火墙配置 =====
echo -e "\n${GREEN}[8/8] 配置防火墙...${NC}"
if command -v ufw &> /dev/null; then
  ufw allow 80/tcp > /dev/null 2>&1
  ufw allow 22/tcp > /dev/null 2>&1
  if ufw status | grep -q "inactive"; then
    echo -e "${YELLOW}防火墙未启用，建议运行 ufw enable${NC}"
  else
    echo -e "${GREEN}防火墙已放行 80 和 22 端口${NC}"
  fi
else
  echo -e "${YELLOW}未检测到 ufw，请手动放行 80 端口${NC}"
fi

# ===== 等待服务启动 =====
echo -e "\n${BLUE}等待服务启动...${NC}"
sleep 3

# 检查服务状态
if systemctl is-active --quiet taiwuyunjing; then
  echo -e "${GREEN}✓ 服务运行正常${NC}"
else
  echo -e "${RED}✗ 服务启动失败，查看日志: journalctl -u taiwuyunjing -f${NC}"
  exit 1
fi

# 获取服务器 IP
SERVER_IP=$(curl -s ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')

echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}  部署完成！${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "${YELLOW}访问地址:${NC}"
echo -e "  登录页:  http://${SERVER_IP}/"
echo -e "  管理后台: http://${SERVER_IP}/admin"
echo ""
echo -e "${YELLOW}管理员账号:${NC}"
echo -e "  用户名: ruiyayouqing"
echo -e "  密码:   luopingwan"
echo ""
echo -e "${YELLOW}常用命令:${NC}"
echo -e "  查看服务状态: systemctl status taiwuyunjing"
echo -e "  查看实时日志: journalctl -u taiwuyunjing -f"
echo -e "  重启服务:     systemctl restart taiwuyunjing"
echo -e "  停止服务:     systemctl stop taiwuyunjing"
echo ""
echo -e "${YELLOW}注意事项:${NC}"
echo -e "  1. 首次使用请用管理员账号登录后台，配置 API Key"
echo -e "  2. 配置文件位于: ${INSTALL_DIR}/config.json"
echo -e "  3. 用户数据存储在: ${INSTALL_DIR}/data/users.db"
echo -e "  4. 每用户存档独立存储在: ${INSTALL_DIR}/data/user_data/"
echo ""
