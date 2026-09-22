/**
 * Check the original repo (charlesdove977/jarvis, the `upstream` remote) for
 * commits this fork doesn't have yet.
 *
 * Read-only: fetches upstream and compares against the current branch, prints
 * what's new, changes nothing. Merging or rebasing those commits in is a
 * separate, deliberate step — this just answers "is there anything to look at."
 */

import { execFileSync } from 'node:child_process'

function git(args) {
  return execFileSync('git', args, { encoding: 'utf8' }).trim()
}

const branch = git(['rev-parse', '--abbrev-ref', 'HEAD'])

const remotes = git(['remote']).split('\n')
if (!remotes.includes('upstream')) {
  console.log(
    '[check-upstream] no "upstream" remote found — this only works on a fork set up the way' +
      ' this repo was (origin = your fork, upstream = charlesdove977/jarvis).',
  )
  process.exit(0)
}

console.log('[check-upstream] fetching upstream...')
git(['fetch', 'upstream', branch])

const ahead = git(['rev-list', '--count', `${branch}..upstream/${branch}`])
const behind = git(['rev-list', '--count', `upstream/${branch}..${branch}`])

if (Number(ahead) === 0) {
  console.log(`[check-upstream] up to date with upstream/${branch} — nothing new.`)
  process.exit(0)
}

console.log(
  `\n[check-upstream] upstream/${branch} has ${ahead} commit${ahead === '1' ? '' : 's'} not in your fork` +
    (Number(behind) > 0 ? ` (your fork also has ${behind} of its own, not on upstream)` : '') +
    ':\n',
)
console.log(git(['log', `${branch}..upstream/${branch}`, '--oneline']))
console.log(
  `\nTo look closer: git log ${branch}..upstream/${branch}` +
    `\nTo pull a specific commit in: git cherry-pick <hash>` +
    `\nTo merge all of it in: git merge upstream/${branch}`,
)
