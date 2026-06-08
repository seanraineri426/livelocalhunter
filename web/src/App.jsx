import { useEffect, useMemo, useState } from 'react'
import './App.css'

const API_URL = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'

const suggestedPrompts = [
  'Summarize eligibility, massing, and biggest diligence gaps.',
  'What assumptions most affect feasibility on this parcel?',
  'What should zoning counsel verify before an LOI?',
]

async function api(path, options = {}) {
  const response = await fetch(`${API_URL}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || `API ${response.status}`)
  }
  return response.json()
}

function money(value) {
  if (value === null || value === undefined) return 'n/a'
  return Number(value).toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
}

function formatAddress(parcel) {
  if (!parcel?.site_address) return ''
  return [parcel.site_address, parcel.site_city, parcel.site_zip].filter(Boolean).join(', ')
}

function formatAcres(parcel) {
  const acres = parcel?.acreage ?? (parcel?.lot_sf ? Number(parcel.lot_sf) / 43560 : null)
  if (acres === null || acres === undefined || Number.isNaN(Number(acres))) return 'acreage n/a'
  return `${Number(acres).toLocaleString(undefined, { maximumFractionDigits: 1 })} ac`
}

function reviewRequired(flags = []) {
  return flags.some((flag) => [
    'oversized_parcel_review_required',
    'manual_site_boundary_required',
    'parcel_zoning_unmatched_review_required',
    'parcel_zoning_qualification_unverified',
    'land_category_from_current_use_or_candidate_bucket',
  ].includes(flag))
}

function eligibilityLabel(parcel) {
  if (parcel?.eligible === false) {
    const reasons = parcel.failed_reasons || []
    return reasons.length ? `ineligible: ${reasons.slice(0, 2).join(', ')}` : 'ineligible'
  }
  if (parcel?.eligible === true) {
    return reviewRequired(parcel.massing_flags || []) ? 'review' : 'eligible'
  }
  return 'not computed'
}

function massingLabel(parcel, contextSummary) {
  if (parcel?.eligible === false || contextSummary?.massing?.applies === false) {
    const reasons = parcel?.failed_reasons || contextSummary?.eligibility?.failed_reasons || []
    return reasons.length ? `not applicable (${reasons[0]})` : 'not applicable'
  }
  if (contextSummary?.massing?.review_required || reviewRequired(parcel?.massing_flags || [])) {
    return 'Review required'
  }
  const units = parcel?.max_units ?? contextSummary?.massing?.max_units
  return units ? `${units} units` : 'n/a'
}

function severityClass(severity) {
  if (severity === 'high') return 'flag high'
  if (severity === 'medium') return 'flag medium'
  return 'flag'
}

function App() {
  const [folio, setFolio] = useState('')
  const [county, setCounty] = useState('miami_dade')
  const [results, setResults] = useState([])
  const [selectedParcelId, setSelectedParcelId] = useState('')
  const [context, setContext] = useState(null)
  const [templates, setTemplates] = useState([])
  const [templateName, setTemplateName] = useState('base_case')
  const [assumptions, setAssumptions] = useState({
    hard_cost_per_gross_sf: '',
    gross_sf: '',
    acquisition_price: '',
    market_monthly_rent: '',
    assessed_value: '',
  })
  const [feasibility, setFeasibility] = useState(null)
  const [costAudit, setCostAudit] = useState(null)
  const [massingAudit, setMassingAudit] = useState(null)
  const [chatMessage, setChatMessage] = useState(suggestedPrompts[0])
  const [chatResponse, setChatResponse] = useState('')
  const [note, setNote] = useState('')
  const [status, setStatus] = useState('unreviewed')
  const [loading, setLoading] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    api('/scenario-templates')
      .then((data) => setTemplates(data.templates || []))
      .catch((err) => setError(err.message))
  }, [])

  const selectedTemplate = useMemo(
    () => templates.find((template) => template.template_name === templateName),
    [templates, templateName],
  )

  const cleanAssumptions = () =>
    Object.fromEntries(
      Object.entries(assumptions)
        .filter(([, value]) => value !== '')
        .map(([key, value]) => [key, Number.isNaN(Number(value)) ? value : Number(value)]),
    )

  async function searchParcels(event) {
    event.preventDefault()
    setLoading('search')
    setError('')
    try {
      const params = new URLSearchParams({ county })
      if (folio) params.set('folio', folio)
      const data = await api(`/parcels/search?${params}`)
      setResults(data.results || [])
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading('')
    }
  }

  async function loadContext(parcelId) {
    setSelectedParcelId(parcelId)
    setLoading('context')
    setError('')
    try {
      const [contextData, auditData] = await Promise.all([
        api(`/parcels/${parcelId}/context`),
        api(`/parcels/${parcelId}/massing-audit`),
      ])
      setContext(contextData)
      setMassingAudit(auditData.massing_audit)
      setFeasibility(null)
      setCostAudit(null)
      setChatResponse('')
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading('')
    }
  }

  async function runFeasibility() {
    if (!selectedParcelId) return
    setLoading('feasibility')
    setError('')
    try {
      const data = await api(`/parcels/${selectedParcelId}/feasibility`, {
        method: 'POST',
        body: JSON.stringify({ template_name: templateName, assumptions: cleanAssumptions() }),
      })
      setFeasibility(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading('')
    }
  }

  async function runCostAudit() {
    if (!selectedParcelId) return
    setLoading('audit')
    setError('')
    try {
      const data = await api(`/parcels/${selectedParcelId}/cost-audit`, {
        method: 'POST',
        body: JSON.stringify({
          template_name: templateName,
          assumptions: cleanAssumptions(),
          feasibility: feasibility?.feasibility,
        }),
      })
      setCostAudit(data.cost_audit)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading('')
    }
  }

  async function runMassingAiAudit() {
    if (!selectedParcelId) return
    setLoading('massing-audit')
    setError('')
    try {
      const data = await api(`/parcels/${selectedParcelId}/massing-audit`, {
        method: 'POST',
        body: JSON.stringify({ use_ai: true }),
      })
      setMassingAudit(data.massing_audit)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading('')
    }
  }

  async function sendChat(prompt = chatMessage) {
    if (!selectedParcelId || !prompt) return
    setLoading('chat')
    setError('')
    try {
      const data = await api(`/parcels/${selectedParcelId}/chat`, {
        method: 'POST',
        body: JSON.stringify({ message: prompt, scenario: feasibility }),
      })
      setChatResponse(data.message)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading('')
    }
  }

  async function saveStatus() {
    if (!selectedParcelId) return
    setLoading('status')
    setError('')
    try {
      await api(`/parcels/${selectedParcelId}/status`, {
        method: 'PATCH',
        body: JSON.stringify({ review_status: status }),
      })
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading('')
    }
  }

  async function saveNote() {
    if (!selectedParcelId || !note) return
    setLoading('note')
    setError('')
    try {
      await api(`/parcels/${selectedParcelId}/notes`, {
        method: 'POST',
        body: JSON.stringify({ note }),
      })
      setNote('')
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading('')
    }
  }

  return (
    <main className="workspace">
      <header className="topbar">
        <div>
          <p className="eyebrow">Live Local Hunter</p>
          <h1>Parcel Workspace</h1>
        </div>
        <span className="api-pill">API {API_URL}</span>
      </header>

      {error && <div className="error">{error}</div>}

      <section className="grid">
        <aside className="panel">
          <h2>Find Parcel</h2>
          <form onSubmit={searchParcels} className="stack">
            <label>
              Folio
              <input value={folio} onChange={(event) => setFolio(event.target.value)} placeholder="Source parcel id" />
            </label>
            <label>
              County
              <select value={county} onChange={(event) => setCounty(event.target.value)}>
                <option value="miami_dade">Miami-Dade</option>
                <option value="broward">Broward</option>
                <option value="palm_beach">Palm Beach</option>
              </select>
            </label>
            <button disabled={loading === 'search'}>{loading === 'search' ? 'Searching...' : 'Search'}</button>
          </form>

          <div className="results">
            {results.map((parcel) => {
              const address = formatAddress(parcel)
              const eligible = parcel.eligible === true
              const needsReview = eligible && reviewRequired(parcel.massing_flags || [])
              return (
                <button
                  className={parcel.parcel_id === selectedParcelId ? 'result selected' : 'result'}
                  key={parcel.parcel_id}
                  onClick={() => loadContext(parcel.parcel_id)}
                >
                  <strong>{parcel.source_parcel_id}</strong>
                  {address && <span>{address}</span>}
                  <span>{formatAcres(parcel)} - {parcel.candidate_bucket || parcel.normalized_use || parcel.use_class || 'use n/a'}</span>
                  <span>
                    {parcel.county_fips} - {eligible ? (needsReview ? 'review massing' : `${parcel.max_units || 'n/a'} max units`) : 'massing n/a'} - {eligibilityLabel(parcel)}
                  </span>
                  {parcel.zoning_code && <span>zoning {parcel.zoning_code}</span>}
                </button>
              )
            })}
          </div>
        </aside>

        <section className="panel span-2">
          <div className="panel-heading">
            <h2>Parcel Intelligence</h2>
            {loading === 'context' && <span>Loading context...</span>}
          </div>
          {context ? (
            <>
              <div className="parcel-header">
                <div>
                  <span>Folio</span>
                  <strong>{context.parcel?.source_parcel_id}</strong>
                </div>
                {formatAddress(context.parcel) && <p>{formatAddress(context.parcel)}</p>}
                <p>{formatAcres(context.parcel)}</p>
              </div>
              {context.summary?.eligibility?.review_required && (
                <div className="warning">
                  Review required before relying on massing: verify subject zoning and define a developable site boundary.
                </div>
              )}
              <div className="cards">
                <div className="mini-card">
                  <span>Eligibility</span>
                  <strong>{context.summary?.eligibility?.status || 'not computed'}</strong>
                  <small>{context.entitlement?.confidence || 'unknown'} confidence</small>
                </div>
                <div className="mini-card">
                  <span>Massing</span>
                  <strong>{massingLabel(context.entitlement, context.summary)}</strong>
                  <small>
                    {context.summary?.massing?.applies === false
                      ? (context.entitlement?.failed_reasons?.[0] || 'ineligible')
                      : `${context.entitlement?.max_height_stories || 'n/a'} stories`}
                  </small>
                </div>
                <div className="mini-card">
                  <span>Jurisdiction</span>
                  <strong>{context.jurisdiction?.name || 'unknown'}</strong>
                  <small>{context.jurisdiction_params?.params_version || 'params missing'}</small>
                </div>
                <div className="mini-card">
                  <span>Market Rent Source</span>
                  <strong>{money(context.latest_market_rent_source?.market_rent_monthly)}</strong>
                  <small>{context.latest_market_rent_source?.source_type || 'not stored'}</small>
                </div>
              </div>
            </>
          ) : (
            <p className="muted">Search and select a parcel to load eligibility, massing, flags, and provenance.</p>
          )}
          {context?.summary?.data_gaps?.length > 0 && (
            <div className="chips">
              {context.summary.data_gaps.map((gap) => <span key={gap}>{gap}</span>)}
            </div>
          )}
        </section>

        <section className="panel">
          <h2>Assumptions</h2>
          <label>
            Template
            <select value={templateName} onChange={(event) => setTemplateName(event.target.value)}>
              {templates.map((template) => (
                <option key={template.template_name} value={template.template_name}>{template.label}</option>
              ))}
            </select>
          </label>
          <p className="muted">{selectedTemplate?.description}</p>
          <div className="form-grid">
            {Object.entries(assumptions).map(([key, value]) => (
              <label key={key}>
                {key.replaceAll('_', ' ')}
                <input
                  value={value}
                  onChange={(event) => setAssumptions((current) => ({ ...current, [key]: event.target.value }))}
                />
              </label>
            ))}
          </div>
          <button onClick={runFeasibility} disabled={!selectedParcelId || loading === 'feasibility'}>
            {loading === 'feasibility' ? 'Running...' : 'Run Feasibility'}
          </button>
        </section>

        <section className="panel">
          <h2>Feasibility</h2>
          {feasibility ? (
            <div className="result-card">
              <span className="badge">{feasibility.feasibility?.result}</span>
              <p>Supportable land value: <strong>{money(feasibility.feasibility?.costs?.supportable_land_value)}</strong></p>
              <p>NOI: <strong>{money(feasibility.feasibility?.income?.noi)}</strong></p>
              <p>Market rent: <strong>{money(feasibility.feasibility?.rents?.market_monthly_rent)}</strong></p>
              <small>{(feasibility.feasibility?.warnings || []).slice(0, 4).join(', ')}</small>
            </div>
          ) : (
            <p className="muted">Run feasibility to see deterministic screening output.</p>
          )}
        </section>

        <section className="panel">
          <h2>Cost Audit</h2>
          <button onClick={runCostAudit} disabled={!selectedParcelId || loading === 'audit'}>
            {loading === 'audit' ? 'Auditing...' : 'Run Cost Audit'}
          </button>
          {costAudit && (
            <div className="result-card">
              <span className="badge">{costAudit.status}</span>
              <p>{(costAudit.findings || []).join(' ') || 'No findings returned.'}</p>
              <small>{(costAudit.caveats || []).join(' ')}</small>
            </div>
          )}
        </section>

        <section className="panel span-2">
          <div className="panel-heading">
            <h2>Massing Sanity / Zoning Audit</h2>
            <span className="badge">{massingAudit?.deterministic?.sanity_status || context?.massing_audit_summary?.sanity_status || 'not loaded'}</span>
          </div>
          <p className="muted">
            Deterministic rules review the stored zoning context and massing output. AI is optional and only explains ambiguity; it is not the calculator or source of truth.
          </p>
          {massingAudit?.deterministic ? (
            <div className="audit-card">
              <p>{massingAudit.deterministic.summary}</p>
              {massingAudit.deterministic.flags.length > 0 ? (
                <div className="flag-list">
                  {massingAudit.deterministic.flags.slice(0, 8).map((flag) => (
                    <div className={severityClass(flag.severity)} key={flag.id}>
                      <strong>{flag.title}</strong>
                      <span>{flag.severity} - {flag.category}</span>
                      <p>{flag.explanation}</p>
                      <small>{flag.recommended_action}</small>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="muted">No deterministic flags returned.</p>
              )}
              {massingAudit.deterministic.buckets?.human_required?.length > 0 && (
                <div className="chips">
                  {massingAudit.deterministic.buckets.human_required.map((item) => <span key={item}>human: {item}</span>)}
                </div>
              )}
              <button onClick={runMassingAiAudit} disabled={!selectedParcelId || loading === 'massing-audit'}>
                {loading === 'massing-audit' ? 'Reviewing...' : 'Run AI Reviewer'}
              </button>
              {massingAudit.ai && (
                <div className="ai-review">
                  <span className="badge">AI {massingAudit.ai.status}</span>
                  <p>{massingAudit.ai.summary || 'No AI summary returned.'}</p>
                  {(massingAudit.ai.findings || []).slice(0, 5).map((finding, index) => (
                    <small key={index}>{typeof finding === 'string' ? finding : JSON.stringify(finding)}</small>
                  ))}
                  {(massingAudit.ai.caveats || []).length > 0 && <small>{massingAudit.ai.caveats.join(' ')}</small>}
                </div>
              )}
            </div>
          ) : (
            <p className="muted">Select a parcel to run deterministic massing sanity checks.</p>
          )}
        </section>

        <section className="panel span-2">
          <h2>Parcel Chat</h2>
          <div className="chips">
            {suggestedPrompts.map((prompt) => (
              <button key={prompt} onClick={() => { setChatMessage(prompt); sendChat(prompt) }}>{prompt}</button>
            ))}
          </div>
          <textarea value={chatMessage} onChange={(event) => setChatMessage(event.target.value)} rows="4" />
          <button onClick={() => sendChat()} disabled={!selectedParcelId || loading === 'chat'}>
            {loading === 'chat' ? 'Asking...' : 'Ask Parcel Assistant'}
          </button>
          {chatResponse && <div className="chat-response">{chatResponse}</div>}
        </section>

        <section className="panel">
          <h2>Notes & Status</h2>
          <label>
            Status
            <select value={status} onChange={(event) => setStatus(event.target.value)}>
              <option value="unreviewed">Unreviewed</option>
              <option value="needs_review">Needs Review</option>
              <option value="watch">Watch</option>
              <option value="pursue">Pursue</option>
              <option value="fail">Fail</option>
            </select>
          </label>
          <button onClick={saveStatus} disabled={!selectedParcelId || loading === 'status'}>Save Status</button>
          <textarea value={note} onChange={(event) => setNote(event.target.value)} rows="4" placeholder="Add internal note" />
          <button onClick={saveNote} disabled={!selectedParcelId || !note || loading === 'note'}>Add Note</button>
        </section>
      </section>
    </main>
  )
}

export default App
