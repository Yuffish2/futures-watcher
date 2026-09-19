# 云端盯盘 · 安装说明（10 分钟搞定，之后不用管你的电脑）

> 作用：GitHub 的服务器每 15 分钟跑一次盯盘脚本（交易时段），把结果写回你的仓库。
> 你的电脑**开机与否都不影响**；手机打开仓库就能看结果，助手也能读。

---

## 一、准备（5 分钟）

1. 注册/登录 GitHub：<https://github.com>（免费，不需要信用卡）
2. 点右上角 **+ → New repository**
   - Repository name 随便填，例如 `futures-watcher`
   - 选 **Private**（私有，只有你能看）
   - 勾选 **Add a README file**
   - 点 **Create repository**

## 二、上传文件（3 分钟）

把本文件夹 `cloud_watcher` 里的**全部内容**上传到刚建的仓库：

方法 A（网页上传，最简单）：
1. 在仓库页点 **Add file → Upload files**
2. 把 `cloud_watcher` 里的这些**拖进去**（注意保持目录结构）：
   - `cloud_watch.py`
   - `requirements.txt`
   - `.gitignore`
   - `data/`（整个文件夹：主力合约.csv、品种产业链.csv、baseline.csv）
   - `out/`（整个文件夹）
   - `.github/workflows/watch.yml` ← **这个最关键，路径不能错**
3. 点 **Commit changes**

> ⚠️ 网页拖拽有时会漏掉 `.github` 这种隐藏文件夹。**上传后务必确认仓库里有 `.github/workflows/watch.yml`**（仓库首页应该能看到 `.github` 目录）。
> 如果漏了：点 **Add file → Create new file**，文件名填 `.github/workflows/watch.yml`，把本文件夹里同名文件的内容粘进去。

方法 B（会用 git 的话）：
```bash
cd cloud_watcher
git init && git add -A && git commit -m "init"
git branch -M main
git remote add origin https://github.com/你的用户名/futures-watcher.git
git push -u origin main
```

## 三、打开自动化（1 分钟）

1. 进入仓库的 **Actions** 标签页
2. 如果看到提示 "Workflows aren't being run on this repository" → 点 **I understand my workflows, go ahead and enable them**
3. 左侧点 **cloud-watcher** → 右边有 **Run workflow** 按钮 → 点一下手动跑一次，验证是否能跑通（跑完约 1 分钟）

## 四、看结果（随时，手机也行）

跑完之后仓库里会出现（每次自动更新）：

| 文件 | 内容 |
|---|---|
| `out/signals.csv` | **信号记录**（品种/方向/入场/止损/目标/风险/链条/时段） |
| `out/snapshot_log.csv` | 每次盯盘的快照（现价/区间/状态/允许方向/风险） |
| `out/待答复_最新.txt` | **最新一条信号**，打开复制给助手即可 |
| `data/baseline.csv` | 基准价（每时段收盘后自动刷新） |

## 五、它是怎么判断的（和本地版完全一致）

- **链条方向**：抓 69 个品种实时价，按链算均值 → 链条为负只做空、为正只做多、**反向直接放弃**
- **开盘区间**：当前时段（日盘 09:00-15:00 / 夜盘 21:00-23:00）前 30 分钟的高低
- **入场**：15 分钟 K **收盘突破**区间 → 按链条方向进场（允许追，规则十短线豁免）
- **风控**：止损 1×ATR(60分钟)，目标 1.5R，**单笔风险 ≤300 元**（超了自动放弃并在日志里标 ❌）
- **白名单**：甲醇 / 豆粕 / 螺纹 / 热卷 / 淀粉

## 六、常见问题

**Q：会不会自动下单？**
不会。它只**记录和提示**，下单永远由你（或券商条件单）执行。

**Q：跑一次占多少额度？**
GitHub 免费账户每月有 2,000 分钟 Actions 额度。本任务每次约 1 分钟、每天约 30 次 → **每月约 600 分钟，在免费额度内**。

**Q：为什么有时候日志里什么都没有？**
非交易时段（周末、非交易时间）K 线不足，会显示"本时段K线不足"——正常。

**Q：想改白名单/参数？**
改 `cloud_watch.py` 顶部的 `WHITE`（品种、链名、合约、乘数、手续费）和 `MAX_RISK`（单笔风险上限）。

**Q：数据源会不会被封？**
用的是新浪财经公开行情接口（和本地脚本同一套），15 分钟一次、每次约 6 个请求，频率很低。

**Q：想加微信/手机推送？**
可以加一个 Step 调用第三方推送（如 Server 酱 / Telegram Bot）。需要你提供推送服务的 token，我可以补上。
