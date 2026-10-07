<script lang="ts">
  import { onMount, type Component } from 'svelte'
  import Notice from '../components/Notice.svelte'
  import { api } from '../lib/api'
  import { errorText } from '../lib/form'
  import { navigate } from '../lib/router.svelte'
  import type { OnboardingState } from '../lib/types'
  import AccountsStep from './welcome/AccountsStep.svelte'
  import AIStep from './welcome/AIStep.svelte'
  import DebtsStep from './welcome/DebtsStep.svelte'
  import FirstUploadStep from './welcome/FirstUploadStep.svelte'
  import GoalsStep from './welcome/GoalsStep.svelte'
  import HomeStep from './welcome/HomeStep.svelte'
  import HouseholdStep from './welcome/HouseholdStep.svelte'
  import WelcomeStep from './welcome/WelcomeStep.svelte'
  import WorkIncomeStep from './welcome/WorkIncomeStep.svelte'

  /** A step may offer `save()`, which Continue calls first; false keeps the user on the step. */
  type StepApi = { save?: () => Promise<boolean> }
  const STEP_COMPONENTS: Record<string, Component<any, StepApi>> = {
    welcome: WelcomeStep, household: HouseholdStep, work_income: WorkIncomeStep, home: HomeStep, accounts: AccountsStep,
    debts: DebtsStep, goals: GoalsStep, ai: AIStep, first_upload: FirstUploadStep,
  }

  let onboarding = $state<OnboardingState | null>(null)
  let index = $state(0)
  let error = $state('')
  let busy = $state(false)
  let stepRef = $state<StepApi | undefined>()

  const steps = $derived(onboarding?.steps ?? [])
  const step = $derived(steps[index])
  const last = $derived(index === steps.length - 1)
  const Step = $derived(step ? STEP_COMPONENTS[step.id] : undefined)

  onMount(async () => {
    try {
      onboarding = await api<OnboardingState>('/api/onboarding')
      // Resume where the user left off; a finished wizard opens at the start.
      const at = onboarding.steps.findIndex((s) => s.id === onboarding?.next_step)
      index = at >= 0 ? at : 0
    } catch (err) { error = errorText(err) }
  })

  async function record(status: 'done' | 'skipped') {
    if (!step || busy) return
    error = ''; busy = true
    try {
      if (status === 'done' && stepRef?.save && !(await stepRef.save())) return
      onboarding = await api<OnboardingState>(`/api/onboarding/steps/${step.id}`, { method: 'POST', body: { status } })
      if (last) navigate('/')
      else index += 1
    } catch (err) { error = errorText(err) } finally { busy = false }
  }

  function back() { error = ''; if (index > 0) index -= 1 }
</script>

<section>
  {#if step}
    <p class="meta" aria-live="polite">Step {index + 1} of {steps.length}</p>
    <progress max={steps.length} value={index + 1} aria-label="Setup progress"></progress>
    <h1>{step.title}</h1>
    <Notice message={error} />
    {#key step.id}
      <Step bind:this={stepRef} />
    {/key}
    <div class="row wizard-nav">
      <button type="button" onclick={back} disabled={index === 0 || busy}>Back</button>
      <button type="button" onclick={() => record('skipped')} disabled={busy}>Skip this step</button>
      <button type="button" onclick={() => record('done')} disabled={busy}>{last ? 'Finish' : 'Continue'}</button>
    </div>
  {:else if error}
    <h1>Welcome</h1>
    <Notice message={error} />
  {:else}
    <p class="status">Loading…</p>
  {/if}
</section>
