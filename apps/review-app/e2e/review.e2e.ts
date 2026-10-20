import { expect, test } from '@playwright/test'
import { ENCOUNTER_ID, mockApi, signIn } from './mock-api'

test('a clinician checks sources, edits, recovers from a conflict and approves', async ({ page }) => {
  const state = await mockApi(page, 'CLINICIAN')
  await signIn(page, 'clinician')

  await page.getByRole('link', { name: 'Synthetic Patient' }).click()
  await expect(page).toHaveURL(new RegExp(`/encounters/${ENCOUNTER_ID}$`))
  await expect(page.getByText('Safety check flagged 1 issue')).toBeVisible()

  // Sources: selecting a plan line highlights the transcript lines it came from.
  await page.getByRole('button', { name: 'Show sources' }).click()
  await page.getByRole('button', { name: /Increase lisinopril to 20 mg daily/ }).click()
  await expect(page.locator('#t-line-3')).toHaveClass(/t-line-hl/)
  await expect(page.locator('#t-line-5')).toHaveClass(/t-line-hl/)
  await expect(page.locator('#t-line-2')).not.toHaveClass(/t-line-hl/)
  await expect(page.getByRole('button', { name: /Start aspirin 81 mg daily/ })).toContainText('No source')
  await expect(page.getByText('1 line cites no source.')).toBeVisible()

  // The reviewer removes the unsourced line and saves a new version.
  await page.getByRole('button', { name: 'Hide sources' }).click()
  const plan = page.getByLabel('Plan')
  await plan.fill('Increase lisinopril to 20 mg daily.\nFollow up in 4 weeks.')
  await expect(page.getByRole('button', { name: 'Approve v1' })).toBeDisabled()
  await page.getByRole('button', { name: 'Save as v2' }).click()
  await expect(page.getByText('Saved as version 2.')).toBeVisible()

  // Sources still apply to the lines the reviewer kept, and the edited note marks nothing unsourced.
  await page.getByRole('button', { name: 'Show sources' }).click()
  await expect(page.getByRole('button', { name: /Follow up in 4 weeks/ })).toContainText('From transcript line 6')
  await expect(page.getByText(/cites? no source/)).toHaveCount(0)
  await page.getByRole('button', { name: 'Hide sources' }).click()

  // Someone else saves first: the edit is rejected, not silently overwritten.
  state.conflictOnNextSave = true
  await plan.fill('Increase lisinopril to 20 mg daily.\nFollow up in 2 weeks.')
  await page.getByRole('button', { name: 'Save as v3' }).click()
  await expect(page.getByText(/changed by someone else.*Reload to see the latest version/)).toBeVisible()

  // Back to the saved version, then approve exactly that version.
  await page.getByRole('button', { name: 'Discard changes' }).click()
  await page.getByRole('button', { name: 'Approve v2' }).click()
  await expect(page.getByText('Version 2 approved.')).toBeVisible()
  expect(state.approvedVersion).toBe(2)
})

test('a scribe can edit but not approve', async ({ page }) => {
  await mockApi(page, 'SCRIBE')
  await signIn(page, 'scribe')
  await page.getByRole('link', { name: 'Synthetic Patient' }).click()
  await expect(page.getByText('Only clinicians can approve.')).toBeVisible()
  await expect(page.getByRole('button', { name: /^Approve/ })).toHaveCount(0)
})

test('the medication list shows what the draft did with each drug', async ({ page }) => {
  await mockApi(page, 'CLINICIAN')
  await signIn(page, 'clinician')
  await page.getByRole('link', { name: 'Synthetic Patient' }).click()
  await page.getByText('Medications in the AI draft (2)').click()
  const row = page.getByRole('row', { name: /aspirin/ })
  await expect(row).toContainText('started')
  await expect(row).toContainText('81 mg')
})
