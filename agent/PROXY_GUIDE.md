# Vibe-Trading 网络代理分流配置指南

## 概述

本项目已集成智能代理分流功能，支持根据目标域名自动选择直连或通过 SSH 跳板代理访问：

- **境内域名**：直接连接，不启用代理
- **境外域名**：通过 SOCKS5H 代理（跳板服务器）连接
- **自动容错**：先尝试直连，失败后自动切换代理重试

## 配置说明

### 环境变量配置

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `SOCKS5_PROXY` | `socks5h://127.0.0.1:1080` | SOCKS5H 代理地址（必须使用 socks5h 协议） |
| `PROXY_TIMEOUT` | `10` | 直连超时时间（秒） |
| `MAX_RETRIES` | `2` | 最大重试次数 |

### 域名清单

**境内域名白名单**（直接连接）：
- 国内云服务商：`aliyun.com`, `baidu.com`, `tencent.com`, `jd.com` 等
- 国内金融数据：`eastmoney.com`, `10jqka.com.cn` 等
- 国内交易所：`sse.com.cn`, `szse.cn`, `cffex.com.cn` 等
- 本地地址：`localhost`, `127.0.0.1`

**境外域名清单**（强制走代理）：
- 境外交易所：`binance.com`, `gate.io`, `okx.com`, `kucoin.com` 等
- 境外AI服务：`openai.com`, `anthropic.com`, `groq.com` 等
- 境外数据服务：`jina.ai`, `duckduckgo.com`, `coingecko.com` 等

## 快速开始

### 1. 启动 SSH 隧道（后台常驻）

```bash
# 建立 SSH 隧道（需提前配置免密登录）
ssh -fN -D 1080 -p 45617 user@138.2.95.116

# 验证隧道是否正常
curl --socks5h localhost:1080 https://api.ipify.org
```

### 2. 启动服务器

```bash
# 方法1：使用启动脚本（推荐）
cd /tmp/Vibe-Trading/agent
chmod +x start_server.sh
./start_server.sh

# 方法2：手动启动
cd /tmp/Vibe-Trading/agent
export SOCKS5_PROXY="socks5h://127.0.0.1:1080"
export PROXY_TIMEOUT="10"
export MAX_RETRIES="2"
PYTHONPATH=/tmp/Vibe-Trading/agent nohup /tmp/Vibe-Trading/venv/bin/python -m api_server --port 8899 --host 0.0.0.0 >> /tmp/vibe-trading-server.log 2>&1 &
```

### 3. 验证配置

```bash
# 检查配置是否生效
curl -s http://localhost:8899/config/debug | python3 -m json.tool

# 测试网络请求
curl -s -X POST http://localhost:8899/api/...
```

## 运维命令

### 启动/停止服务

```bash
# 启动（后台）
./start_server.sh

# 停止服务
kill $(cat /tmp/vibe-trading-server.pid)

# 强制停止
kill -9 $(cat /tmp/vibe-trading-server.pid)
```

### 查看日志

```bash
# 实时查看日志
tail -f /tmp/vibe-trading-server.log

# 查看代理相关日志
grep -i "proxy\|socks" /tmp/vibe-trading-server.log

# 查看网络请求日志
grep -i "read_url\|GET\|POST" /tmp/vibe-trading-server.log
```

### 验证 SSH 隧道

```bash
# 检查隧道进程
ps aux | grep ssh | grep 1080

# 测试代理连通性
curl --socks5h localhost:1080 https://www.google.com > /dev/null && echo "代理正常" || echo "代理异常"

# 测试直连连通性
curl https://www.baidu.com > /dev/null && echo "直连正常" || echo "直连异常"
```

## 域名清单修改指南

### 修改境内域名白名单

编辑以下文件中的 `CHINA_DOMAINS` 或 `_CHINA_DOMAINS` 变量：

- `api_server.py`（第71-83行）
- `src/tools/web_reader_tool.py`（第31-37行）

```python
# 添加新的境内域名
CHINA_DOMAINS = {
    "existing-domain.com",
    "new-domain.cn",  # 新增
}
```

### 修改境外域名清单

编辑以下文件中的 `FOREIGN_DOMAINS` 或 `_FOREIGN_DOMAINS` 变量：

- `api_server.py`（第86-102行）
- `src/tools/web_reader_tool.py`（第39-50行）
- `backtest/loaders/ccxt_loader.py`（第53-57行）

```python
# 添加新的境外域名
FOREIGN_DOMAINS = {
    "existing-domain.com",
    "new-foreign-domain.com",  # 新增
}
```

## 故障排查

### 问题1：代理连接失败

**现象**：日志中出现 `Connection refused` 或 `Proxy error`

**排查步骤**：

```bash
# 1. 检查 SSH 隧道是否运行
ps aux | grep ssh | grep 1080

# 2. 重启隧道
killall -9 ssh
ssh -fN -D 1080 -p 45617 user@138.2.95.116

# 3. 测试代理连通性
curl --socks5h localhost:1080 https://api.ipify.org
```

### 问题2：请求超时

**现象**：日志中出现 `timeout` 或 `Request timed out`

**排查步骤**：

```bash
# 1. 增加超时时间
export PROXY_TIMEOUT="30"

# 2. 检查网络延迟
ping api.ipify.org
curl -I https://api.ipify.org

# 3. 检查跳板服务器网络
ssh user@138.2.95.116 "ping 8.8.8.8"
```

### 问题3：DNS 解析失败

**现象**：日志中出现 `DNS resolution failed` 或域名无法解析

**排查步骤**：

```bash
# 1. 确认使用的是 socks5h 协议（不是 socks5）
echo $SOCKS5_PROXY  # 应显示 socks5h://...

# 2. 在跳板服务器上测试 DNS
ssh user@138.2.95.116 "nslookup api.ipify.org"

# 3. 重新启动服务器（确保配置生效）
./start_server.sh
```

## 技术实现

### 代理分流逻辑

```
请求发起
    │
    ▼
解析目标域名
    │
    ├── 境内域名？───是───► 直接连接
    │
    ├── 境外域名？───是───► 使用 SOCKS5H 代理
    │
    └── 未知域名 ────────► 默认使用代理（保守策略）
```

### 重试机制

```
第1次尝试 ──失败──► 等待 2秒 ──► 第2次尝试 ──失败──► 等待 5秒 ──► 第3次尝试
    │                                      │
    └─────────────成功─────────────────────┘
```

## 安全注意事项

1. **SSH 密钥安全**：确保私钥文件权限为 `600`
2. **代理协议**：必须使用 `socks5h` 协议，禁止使用普通 `socks` 协议
3. **DNS 解析**：境外域名的 DNS 解析在跳板服务器完成，避免国内 DNS 污染
4. **日志审计**：定期检查日志，监控异常请求模式

---

**最后更新**：2024年  
**适用版本**：Vibe-Trading v5.x
