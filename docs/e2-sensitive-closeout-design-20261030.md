# E2 设计：ConfigHub 敏感隔离收尾（2026-10-30）

> 依据：`stage-e-plan-20261030.md` §2 E2 + 用户红线「绝不打印 key 明文，
> 显示时必须脱敏」。三道门沿用。**小刀（0.5d）：两处改动 + 测试。**

---

## 1. 勘查结论

| # | 事实 | 判定 |
|---|---|---|
| 1 | 全库扫描 `api_key` × 事件/打印构造点：`env:` 间接引用 ✅、来源标注 ✅、HTTP Authorization 头（本该如此）✅ | 无泄漏路径 |
| 2 | **唯一瑕疵**：`config_audit.py:148` C1 告警打印 `llm['api_key'][:8]`（明文前 8 字符） | 违反脱敏红线 → 本刀修 |
| 3 | `config_fingerprint()` 已存在（sha256 · SENSITIVE_KEYS 剔除）但**零调用方**——又一个「建而未接线」 | 本刀接线 |
| 4 | `PlatformConfig`（gateway/mailbox 段）**不含任何密钥字段**——密钥住 LLMConfig | SENSITIVE_KEYS 保持空 + 注释说明；LLM 侧指纹由 1 的 helper 承担 |

## 2. 改动

### 2.1 `config_audit.py`：8 字符明文 → sha256 指纹

```python
def _key_fingerprint(value: str) -> str:
    """脱敏指纹：sha256 前 12 hex。可对账（同 key 同指纹）、不可还原。"""
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
```

C1 description 变为：
`"api_key in llm.json is plaintext (fingerprint {_key_fingerprint(key)}). ..."`

- 既有 C1 测试只断言 code，不锁描述 → 不破坏；
- 新增断言：描述**不含 key 任何前缀**、指纹确定（同 key 同指纹）。

### 2.2 `gateway.py` + `server.py`：指纹接进事件流

```python
# Gateway
def emit_config_fingerprint(self) -> str | None:
    """把平台配置指纹写入事件流（audit 对账用；不含配置内容本身）。"""
    from .orchestration.config_hub import config_fingerprint, load_platform_config
    try:
        fp = config_fingerprint(load_platform_config())
        self._combined_sink("config.fingerprint", {"fingerprint": fp})
        return fp
    except Exception:
        logger.debug("config fingerprint emit failed", exc_info=True)
        return None
```

`server.py` 宿主：`gw.emit_config_fingerprint()`（失败只降级，不阻断启动）。

### 2.3 SENSITIVE_KEYS 保持空（注释说明）

`PlatformConfig` 今天不含密钥字段；LLM 密钥不在 ConfigHub 辖区。
SENSITIVE_KEYS 的剔除机制保留，供未来加入敏感字段时使用。

## 3. 测试（`tests/test_e2_sensitive.py`）

| # | 用例 | 断言 |
|---|---|---|
| 1 | C1 描述含 sha256 指纹、不含明文前缀 | `"sk-cp-1234" not in desc` |
| 2 | 指纹确定性 | 同 key 两次指纹相同；不同 key 不同 |
| 3 | emit_config_fingerprint | 发出 `config.fingerprint` 事件，字段只有 fingerprint |
| 4 | fingerprint 剔除敏感键 | 构造含敏感字段的 config → 指纹不受其影响（机制有效性） |

## 4. 验收对照

| 门 | 判定 |
|---|---|
| 行为等价 | C1 告警仍发出（code/severity/fixable 不变），只有描述里的明文前缀变指纹 |
| 测试不减 | inventory 0 removed |
| 失败集不扩大 | c1_gate 对基线 |
