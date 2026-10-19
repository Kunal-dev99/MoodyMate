import { describe, expect, it } from 'vitest'
import { actorText, describeActor, isSeedActor } from '@/components/common/ActorLabel'

const ME = '11111111-2222-3333-4444-555555555555'
const OTHER = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'

describe('describeActor (P-1)', () => {
  it('labels the signed-in user as You', () => {
    expect(describeActor(`user:${ME}`, ME)).toEqual({ kind: 'you', label: 'You', title: `user:${ME}` })
  })
  it('shortens another user and keeps the full id in the title', () => {
    expect(describeActor(`user:${OTHER}`, ME)).toEqual({ kind: 'user', label: 'User · aaaaaaaa', title: `user:${OTHER}` })
  })
  it.each(['system:migration', 'system:dev-session', '0018_calculations', '0005_classification_seed'])(
    'treats %s as platform seed',
    (actor) => {
      expect(isSeedActor(actor)).toBe(true)
      expect(describeActor(actor, ME).label).toBe('Platform seed')
      expect(describeActor(actor, ME, { seedLabel: 'Platform seed (illustrative)' }).label).toBe('Platform seed (illustrative)')
    },
  )
  it('passes anything else through', () => {
    expect(describeActor('someone', ME)).toEqual({ kind: 'other', label: 'someone', title: 'someone' })
    expect(isSeedActor('user:0018_calculations')).toBe(false)
  })
  it('renders null as an em dash in text form', () => {
    expect(actorText(null, ME)).toBe('—')
    expect(actorText(`user:${ME}`, null)).toBe('User · 11111111')
  })
})
