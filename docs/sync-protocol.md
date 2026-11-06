# ORBYTE 同步接口契约（v1.1 · 2026-10-29）

> 本文与代码逐字对应：`server/modules/orbyte/orbyte.controller.ts`、
> `ORBYTE/src/orbyte/api_client.py`。改接口必须同时改三处，否则下一个人会照着旧文档写。

所有接口在 `/api/orbyte/` 下。除 `pair/start`、`pair/poll` 外都要
`Authorization: Bearer dtk_...`（服务端只存 sha256，明文只在设备上）。

时间统一 ISO-8601 带时区（`2026-10-29T17:12:00+08:00`）。字段一律 snake_case。

---

## 1. 配对

### POST /pair/start（免登录）

```json
{ "device_name": "LENOVO-PC", "platform": "Windows 11", "token_hash": "<sha256 hex>", "app_version": "1.0.0" }
```

```json
{ "code": "482913", "expires_at": "2026-10-29T09:20:00.000Z", "confirm_url": "https://…/desktop-pair?code=482913" }
```

- 令牌由**设备自己生成**（`dtk_` + 48 hex），只把 sha256 交上来；库被拖走也拿不到可用令牌
- 6 位码从 100000 起（避免前导零），10 分钟有效，全局唯一
- 同一 token_hash 重复发起：作废旧码、复用同一行设备记录

### GET /pair/poll（免登录）

两种问法：`?code=482913` 或 `?token_hash=<hex>`。

```json
{ "status": "pending|active|expired|revoked", "device_id": null, "device_name": null, "user_name": null }
```

网页确认后码即作废（一码一用），此时必须改用 `token_hash` 问，否则永远看到 `expired`。

### POST /pair/confirm（网页登录态）

```json
{ "code": "482913" }
```

```json
{ "device_id": "uuid", "device_name": "LENOVO-PC", "platform": "Windows 11", "status": "active" }
```

---

## 2. 推送

### POST /sync/todos

```json
{
  "batch": [
    {
      "seq": 128,
      "uuid": "8f3e…",
      "title": "写周报",
      "project": "日常事务",
      "state": "todo|doing|done",
      "space": "work",
      "position": 3,
      "started_at": null,
      "spent_sec": 1500,
      "priority": "P1",
      "due_date": "2026-11-02",
      "planned_min": 60,
      "updated_at": "2026-10-29T17:12:00+08:00",
      "deleted_at": null
    }
  ]
}
```

```json
{ "acked_upto": 128, "accepted": 1 }
```

**关键语义**

- `acked_upto`：已落库的最大 seq。客户端只把 `seq <= acked_upto` 标 acked，其余退回 pending 重发 —— 宁可重发，不可漏发
- 幂等：按 `(user_id, uuid)` upsert，同一条推十次结果一样
- 整批事务：要么全进要么全不进，所以 acked 只能是整批的最大 seq
- **只收 work**：任一条 `space != 'work'`（含缺省）整批 400，服务端记日志。不静默丢弃，否则客户端会以为成功
- 单批上限 200 条
- 服务端给每条分配单调递增的 `revision`（`desktop_sync_state` 计数），供拉取使用

---

## 3. 拉取

### GET /sync/pull?cursor=<revision>

```json
{
  "items": [ { "uuid": "…", "title": "…", "state": "todo", "priority": "P2", "due_date": null, "planned_min": 0, "spent_sec": 0, "updated_at": "…", "deleted_at": null, "revision": 1031 } ],
  "next_cursor": 1031,
  "has_more": false
}
```

- 只返回本人、`revision > cursor` 的记录，按 revision 升序，单页 500 条
- 软删除的记录也会返回（`deleted_at` 非空），否则客户端永远不知道"那条被删了"
- 游标是服务端整数，**不是时间戳**：客户端时钟不可信
- 客户端把拉下来的行标 `synced` 且**不排进 outbox**（否则会回声）
- 冲突：本地有未推送改动且 `updated_at` 更新 → 保留本地，下一轮推送覆盖服务端

---

## 4. 发布包

### GET /release（登录态）

```json
{ "version": "1.0.0", "url": "/api/orbyte/package", "size": 54321123, "sha256": "…", "file_name": "ORBYTE-1.0.0.exe", "published_at": "…", "notes": "…" }
```

服务端扫描 `ORBYTE_RELEASE_DIR`（未配置则 `<cwd>/ORBYTE/release`），取 mtime 最新的
`ORBYTE-*.exe`，版本号从文件名解析。前端与客户端只认 `url`，**任何地方都不出现 `/files/orbyte/xxx.exe` 字面量**。

### GET /package（登录态）

直接回传 exe（`Content-Disposition: attachment`）。前端下载按钮用它。

> 客户端的「检查更新」只比较版本号并打开下载页，**不做静默替换**：
> 运行中的 exe 被占用，静默替换失败会留下打不开的程序。

---

## 5. 设备管理

### GET /devices

```json
{ "items": [ { "id": "uuid", "device_name": "LENOVO-PC", "platform": "Windows 11", "status": "active", "app_version": "1.0.0", "last_seen_at": "…", "created_at": "…" } ] }
```

### DELETE /devices/:id

置 `status='revoked'`。设备下一次请求收到 401，插件提示「需重新配对」。

---

## 6. 网页只读副本

### GET /todos

```json
{ "items": [ { "uuid": "…", "title": "…", "project": "…", "state": "todo", "priority": "P1", "due_date": "2026-11-02", "planned_min": 60, "spent_sec": 1200, "updated_at": "…" } ] }
```

网页 `/desktop-todos` 用它。**只读**：写入口只有桌面端一个，避免两边都能改导致的冲突合并。

---

## 7. 错误体

```json
{ "error": { "code": "space_not_allowed", "message": "只接受 space=work 的待办" } }
```

客户端只按 `status` 与 `message` 提示，不解析文案细节。
