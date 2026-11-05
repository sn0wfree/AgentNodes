# 策略名自动生成规则

## 格式

```
{abbreviated_name}_{user_id}_{compressed_session_ts}_{random2}
```

**示例：**
```
AshareMom_admin_K7f3a9zX4b2m_47
```

## 字段说明

| 字段 | 长度 | 来源 | 示例 |
|------|------|------|------|
| abbreviated_name | ~10 | 从研究目标提取 + 缩写 | `AshareMom` |
| user_id | ~5 | 当前登录用户 | `admin` |
| compressed_session_ts | 12 | session_hash7 + timestamp 合并压缩 | `K7f3a9zX4b2m` |
| random2 | 2 | 字母数字混合随机 | `47` |
| **总计** | **~29** | | |

## 时间戳压缩

`YYMMDDhhmmss` (12 chars) → base36 (7 chars)

| 字段 | 范围 | bits |
|------|------|------|
| YY | 00-99 | 7 |
| MM | 01-12 | 4 |
| DD | 01-31 | 5 |
| hh | 00-23 | 5 |
| mm | 00-59 | 6 |
| ss | 00-59 | 6 |
| **总计** | | **33** |

33 bits → base36 → 7 字符

## Session + Timestamp 合并压缩

session_hash7 (36 bits) + timestamp (33 bits) = 69 bits → base62 → 12 字符

```
压缩: (session_int << 33) | time_int → combined → base62
解压: combined >> 33 → session_int, combined & mask → time_int
```

## 关键词缩写映射

```typescript
const KEYWORD_MAP: Record<string, string> = {
  // 资产类型
  'a股': 'Ashare', '沪深': 'Ashare',
  '美股': 'Ustock', '纳斯达克': 'Ustock',
  '港股': 'Hkstock',
  '加密货币': 'Crypto', '比特币': 'Btc',
  
  // 因子类型
  '动量': 'Mom', '动量因子': 'Mom',
  '波动率': 'Vol', '波动': 'Vol',
  '价值': 'Val', '价值因子': 'Val',
  '质量': 'Qual', '质量因子': 'Qual',
  '反转': 'Rev', '均值回归': 'MeanRev',
  '多因子': 'Multi', '因子组合': 'Combo',
  '轮动': 'Rot', '行业轮动': 'SectorRot',
  '行业': 'Sector',
  
  // 动作
  '测试': 'Test', '验证': 'Validate',
  '研究': 'Rsrch', '优化': 'Opt',
  
  // 过滤词
  '因子': '', '策略': '', '组合': '', '的': '',
}
```

## 生成流程

1. 用户输入研究目标
2. 提取关键词 + 缩写 → 策略名前缀
3. 获取 user_id
4. 生成 session_hash7 (SHA256 前7位)
5. 生成压缩时间戳 (base36)
6. 合并压缩 session_hash7 + timestamp → 12 chars base62
7. 生成 random2 (2位字母数字)
8. 唯一性检查 (重名则重新生成 random2)
9. 显示给用户，可编辑

## 实现文件

| 文件 | 说明 |
|------|------|
| `utils/strategyNameGenerator.ts` | 关键词提取 + 命名生成 + 压缩 |
| `components/study/StrategyNameInput.tsx` | 策略名输入组件 |
| `StudyCreateForm.tsx` | 集成 StrategyNameInput |
| `api/routers/strategy.py` | 策略检查 API |
| `api/client.ts` | 新增 strategy API |
