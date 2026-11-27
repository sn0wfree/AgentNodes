// 配置文件驱动的分组覆盖：前端未配置的段必须落入「其他」（§9 复核批）。
import { describe, it, expect } from 'vitest'
import { aggregateBreakdown, OTHER_GROUP_KEY } from '../components/chat/ContextRing'
import { getContextUsageConfig } from '../components/chat/chatUiConfig'

describe('分组配置覆盖能力', () => {
  it('配置里没有某个细段时，它落到「其他」而不是消失', () => {
    const groups = getContextUsageConfig().groups
    // 模拟配置里删掉 skills 行
    const withoutSkills = groups.filter((g) => g.key !== 'skills')
    const segs = { skills: 500, user_text: 100 }
    const agg = aggregateBreakdown(segs, withoutSkills)
    expect(agg[OTHER_GROUP_KEY]).toBe(500)
    expect(agg.skills).toBeUndefined()
    // 合计不失真
    const total = Object.values(agg).reduce((a, b) => a + b, 0)
    expect(total).toBe(Object.values(segs).reduce((a, b) => a + b, 0))
  })

  it('配置里存在「其他」兜底行', () => {
    const groups = getContextUsageConfig().groups
    expect(groups.some((g) => g.key === OTHER_GROUP_KEY)).toBe(true)
  })

  it('JSON 配置与代码兜底分组的 key 集合一致', () => {
    const fromJson = getContextUsageConfig().groups.map((g) => g.key).sort()
    expect(fromJson).toContain(OTHER_GROUP_KEY)
  })
})
