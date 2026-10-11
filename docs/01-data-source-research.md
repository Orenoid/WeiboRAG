# 微博数据采集调研（首页关注流 + 单条微博内容）

> 调研时间：2026-10-11
> 核心证据来源：微博开放平台官方 wiki、RSSHub 生产代码（`lib/routes/weibo/`，至今仍在维护）

## TL;DR

| 方案 | 多用户（任何人授权） | 自用（开发者自己） | 可行性结论 |
|---|---|---|---|
| ① 官方开放平台 OAuth | ✅ 理论上支持 | ✅ | 接口文档仍在、`home_timeline` 仍列为**普通接口**；**最大风险是新应用审核近年基本停滞**，需实测 |
| ② Cookie 抓取私有接口 | ⚠️ 需用户手动粘贴 Cookie（体验差） | ✅✅ | **已实测可用（weibo.com/ajax/\*），最快落地**，推荐起步方案 |
| ③ 自部署 RSSHub 当数据源 | 同② | ✅ | 几乎零开发成本，RAG 项目直接消费其输出 |

---

## 方案一：微博开放平台官方 API（多用户理想方案）

### 流程

1. 在 [open.weibo.com](https://open.weibo.com/development) 登录微博账号成为开发者，创建应用，拿到 `App Key` / `App Secret`
2. 标准 OAuth2 授权：
   - 跳转 `https://api.weibo.com/oauth2/authorize?client_id=<APP_KEY>&redirect_uri=<CALLBACK>`
   - 回调拿 `code`，POST `https://api.weibo.com/oauth2/access_token` 换 `access_token` + `uid` + `expires_in`
3. 拉关注首页流：
   ```
   GET https://api.weibo.com/2/statuses/home_timeline.json
       ?access_token=<TOKEN>&count=100&since_id=<上次最大微博ID>
   ```
   - `count` 单页最大 100；支持 `since_id` / `max_id` 增量翻页
   - 返回字段含正文、图片 ID、转发链（`retweeted_status`）、计数等
4. 单条微博：`GET https://api.weibo.com/2/statuses/show.json?id=<id>`

### 有效性证据

- 官方 wiki「微博API」页（2025-05-09 更新）仍列出 `statuses/home_timeline`，访问级别标注为**普通接口**
- RSSHub 的 `/weibo/timeline/:uid` 路由（[timeline.ts](https://github.com/DIYgod/RSSHub/blob/master/lib/routes/weibo/timeline.ts)）就是这套 OAuth + `home_timeline` 的完整实现，仍在维护，且文档写明"自部署需要申请并配置微博 key"（申请地址 https://open.weibo.com/connect 仍可访问）

### 风险与限制（重要）

- **新应用审核**：社区共识是开放平台近年处于维护状态，新应用创建/审核通过困难。**需要用自己的微博账号实测**（创建应用 → 看能否提交审核 → 能否通过）。老应用不受影响。
- **未过审应用的致命限制**：官方文档原文——`home_timeline`「只返回授权用户的微博，非授权用户的微博将不返回」。即测试阶段的时间线里**只有你关注的、且也授权了你应用的人**发的微博才会出现（≈ 流是空的）。未过审应用最多添加 15 个测试账号。
- **Token 有效期**：以 `expires_in` 为准（网页应用历史上为 30 天），到期需用户重新授权；RSSHub 的做法是缓存 token，失效时重跳授权页。
- 有接口频次限制，未过审应用额度更低。

**结论**：这是唯一"任何用户都能授权"的正道，但赌在应用审核上。建议先实测一次申请流程；如果走不通，多用户产品的兜底是让用户自己粘贴 Cookie（同方案二，RSSHub 对 `WEIBO_COOKIES` 就是这么干的）。

---

## 方案二：Cookie 抓取私有接口（自用，推荐起步）

> **✅ 已实测通过（2026-10-11）**：`scripts/test_feed_friends.py` 用 weibo.com 桌面版 Cookie 成功抓取关注流（24 条/页、`max_id` 翻页零重复）并补拉长微博全文（截断 160 字 → 全文 398 字）。
>
> **实测修正**：桌面版 Cookie 对 **m.weibo.cn 移动端接口无效**（`api/config` 返回 `login:false`，`feed/friends` 返回 `ok=-100`，移动端需要它自己的登录态）；但同一 Cookie 走 **weibo.com/ajax/\* 桌面接口直接可用**，且自带 `since_id`/`max_id` 双向分页，比移动端接口更适合本项目。**本项目以桌面接口为准**，下表中移动端接口仅作备选（若想用需单独登录 m.weibo.cn 抓 Cookie）。

### 桌面端接口（✅ 实测可用）

| 用途 | 接口 |
|---|---|
| **关注首页流** | `GET https://weibo.com/ajax/feed/friendstimeline?count=25[&max_id=<上一页max_id_str>][&since_id=<上次最大id>]` |
| **单条微博 / 长微博全文** | `GET https://weibo.com/ajax/statuses/show?id=<bid>&isGetLongText=true` |

**请求头**（缺一可能被拒）：

```
Cookie: <weibo.com 登录后的完整 Cookie，必须含 SUB 和 XSRF-TOKEN>
X-Xsrf-Token: <Cookie 中 XSRF-TOKEN 的值>
X-Requested-With: XMLHttpRequest
Referer: https://weibo.com/
User-Agent: 桌面浏览器 UA
```

**响应要点**：

- 分页：返回 `since_id_str` / `max_id_str` / `total_number`（≈2000 条上限）；增量轮询用首页即可，回填历史用 `max_id` 翻页
- 字段：`idstr`（数字 ID）、`mblogid`（即 bid，调 `statuses/show` 用它）、`isLongText`、`pic_ids`、`retweeted_status`、`created_at`（格式 `Sun Oct 11 08:55:39 +0800 2026`）
- `isLongText=True` 时 `text` 被截断，用 `statuses/show?isGetLongText=true` 拿全文 HTML
- 注意：`/ajax/statuses/longtext?id=` 实测返回空内容，**不要用**，用 `show?isGetLongText=true`

### 移动端接口（备选，RSSHub 生产验证）

RSSHub `/weibo/friends`、`/weibo/group` 路由（[friends.ts](https://github.com/DIYgod/RSSHub/blob/master/lib/routes/weibo/friends.ts)）在生产环境验证了以下接口，需要 **m.weibo.cn 自己的登录 Cookie**（桌面 Cookie 不通）：

### 核心接口（JSON 返回）

| 用途 | 接口 |
|---|---|
| **关注首页流（"最新关注"时间线）** | `GET https://m.weibo.cn/feed/friends` |
| 自定义分组流 | `GET https://m.weibo.cn/feed/group?gid=<gid>` |
| 当前登录用户信息（拿 uid） | `GET https://m.weibo.cn/api/config` |
| 用户信息 | `GET https://m.weibo.cn/api/container/getIndex?type=uid&value=<uid>` |
| **单条微博详情 / 长微博全文** | `GET https://m.weibo.cn/statuses/show?id=<bid>` |
| 单条微博（桌面版备用，含 `isGetLongText`） | `GET https://weibo.com/ajax/statuses/show?id=<bid>&isGetLongText=true` |
| 热门评论 | `GET https://m.weibo.cn/comments/hotflow?id=<id>&mid=<mid>&max_id_type=0` |

### 请求头要求

```
Cookie: <登录后的完整 Cookie>
Referer: https://m.weibo.cn/          （单条微博用 https://m.weibo.cn/u/<uid>）
User-Agent: 手机版 UA（如 iPhone Safari）
MWeibo-Pwa: 1
X-Requested-With: XMLHttpRequest
```

### 错误信号

- 响应 `{"ok": -100}` → **Cookie 失效**，需更新
- HTTP `432` → 触发风控/限流，需降速（RSSHub 对此专门做了重试与 cookie 轮换）

### Cookie 获取方式

- **桌面版（本项目用）**：浏览器登录 https://weibo.com → F12 → Network → 刷新首页 → 找任意 `weibo.com/ajax/*` 请求 → 复制请求头完整 `Cookie`
- **移动版（备选接口用）**：浏览器打开并登录 https://m.weibo.cn（确保是手机版，必要时用改 UA 的插件）→ F12 → Network → 切换任意关注分组，找到含 `/feed/group` 的请求 → 复制 `Cookie`

**有效期**：RSSHub 原文「微博 cookies 的过期与更新方案未经验证，部署一次 Cookie 的有效时长未知」。实际经验是数天到数周，工程上必须做"失效检测 + 提醒重填"。

### 待实测确认的点

- ~~关注流翻页~~ ✅ 已验证：`max_id` 翻页有效、零重复
- 单次轮询间隔的安全下限（建议 ≥ 5 分钟，配合指数退避处理 432）
- Cookie 实际有效时长（观察 `ok != 1` / 401 出现的周期）

### 长微博处理

时间线返回中 `isLongText: true` 的微博正文会被截断，需用 `m.weibo.cn/statuses/show?id=<bid>` 二次拉取全文（`text` 字段即完整 HTML 正文）。转发链里的长微博同样要补拉。RSSHub 的 [utils.ts](https://github.com/DIYgod/RSSHub/blob/master/lib/routes/weibo/utils.ts) 里 `getShowData()` / `formatExtended()` 是文本清洗的极佳参考（表情转文字、话题提取、外链还原、多图拼接、转发嵌套渲染）。

---

## 方案三：直接复用 RSSHub 作为采集层

自部署一个 RSSHub 实例，配置 `WEIBO_COOKIES` 后用 `/weibo/friends` 路由输出关注流 RSS，RAG 项目定时消费并解析。优点：采集、清洗、长文本补全全都不用写；缺点：多一层依赖、只能拿首页一屏（无翻页）、正文被转成 RSS 的 description HTML。

适合快速验证整个 RAG 链路，之后再换成方案二的直接采集。

---

## 其他相关项目（参考但不直接适用）

| 项目 | 能力 | 是否含首页关注流 |
|---|---|---|
| [RSSHub](https://github.com/DIYgod/RSSHub) weibo 路由 | 关注流/分组/用户微博/搜索/收藏 | ✅（本调研主要证据） |
| [weibo-crawler](https://github.com/dataabc/weibo-crawler) | 抓**指定用户自己发的**微博（走 `m.weibo.cn/api/container/getIndex?containerid=107603<uid>`） | ❌ |
| [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) | 微博关键词搜索、帖子详情、评论 | ❌ |

---

## 对 weibo-rag 项目的落地建议

1. **采集层（先自用）**：Cookie + `weibo.com/ajax/feed/friendstimeline` 定时轮询（5~10 min），按 `idstr` 去重落库原始 JSON；`isLongText` 的用 `statuses/show?isGetLongText=true` 补拉全文。参考实现：`scripts/test_feed_friends.py`。
2. **清洗层**：正文 HTML → 纯文本（保留话题、`@`、外链），转发微博将原文与转发语拼接为一条文档。直接借鉴 RSSHub `formatExtended` 的规则。
3. **入库**：`{weibo_id, uid, author, created_at, text, retweeted_text, pics, url}` + embedding 向量。
4. **多用户化（后置）**：先实测 open.weibo.com 应用审核能否通过；能通就走官方 OAuth（方案一），不能通就做"用户粘贴 Cookie"模式（注意提示 Cookie 的安全风险，服务端加密存储）。

### 风险备忘

- Cookie 抓取违反微博用户协议，仅限个人自用；账号有被风控（432/封号）的风险，建议用小号测试、低频轮询。
- 接口均为微博私有接口，随时可能变更或加签，需要把"接口失效检测"做进采集层（如连续 N 次 432/-100 报警）。
