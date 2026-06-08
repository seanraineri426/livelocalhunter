import { useEffect, useMemo, useState } from 'react'
import { ParcelMap } from './components/ParcelMap'
import { API_URL, api } from './lib/api'
import {
  COUNTY_LABELS,
  eligibilityLabel,
  eligibilityTone,
  formatAcres,
  formatAddress,
  massingLabel,
  money,
  number,
  reviewRequired,
  title,
} from './lib/format'
import './App.css'

const suggestedPrompts = [
  'Summarize eligibility, massing, and biggest diligence gaps.',
  'What assumptions most affect feasibility on this parcel?',
  'What should zoning counsel verify before an LOI?',
  'Draft a concise investment committee note from stored facts.',
]

function severityClass(severity) {
  if (severity === 'high') return 'flag high'
  if (severity === 'medium') return 'flag medium'
  return 'flag low'
}

function auditTone(audit) {
  const status = audit?.sanity_status
  const flags = audit?.flags || []
  if (status === 'likely_bad_input' || flags.some((flag) => flag.severity === 'high')) return 'danger'
  if (status === 'review' || flags.some((flag) => flag.severity === 'medium')) return 'warning'
  return 'neutral'
}

function confidenceBand(confidence) {
  if (!confidence) return 'Needs Verification'
  const normalized = String(confidence).toLowerCase()
  if (normalized.includes('high')) return 'Known'
  if (normalized.includes('medium') || normalized.includes('estimated')) return 'Estimated'
  return 'Needs Verification'
}

function StatusPill({ children, tone = 'neutral' }) {
  return <span className={`pill ${tone}`}>{children}</span>
}

function SearchPanel({ county, folio, loading, results, selectedParcelId, onCountyChange, onFolioChange, onSearch, onSelect }) {
  return (
    <section className="command-card">
      <div>
        <p className="eyebrow">Command search</p>
        <h2>Find a parcel</h2>
      </div>
      <form onSubmit={onSearch} className="search-row">
        <label>
          Folio
          <input value={folio} onChange={(event) => onFolioChange(event.target.value)} placeholder="3530210010010" />
        </label>
        <label>
          County
          <select value={county} onChange={(event) => onCountyChange(event.target.value)}>
            <option value="miami_dade">Miami-Dade</option>
            <option value="broward">Broward</option>
            <option value="palm_beach">Palm Beach</option>
          </select>
        </label>
        <button className="primary-action" disabled={loading === 'search'}>
          {loading === 'search' ? 'Searching...' : 'Search'}
        </button>
      </form>
      <div className="results">
        {results.map((parcel) => {
          const eligible = parcel.eligible === true
          const needsReview = eligible && reviewRequired(parcel.massing_flags || [])
          const tone = parcel.eligible === false ? 'danger' : needsReview ? 'warning' : eligible ? 'success' : 'neutral'
          return (
            <button
              className={parcel.parcel_id === selectedParcelId ? 'result selected' : 'result'}
              key={parcel.parcel_id}
              onClick={() => onSelect(parcel.parcel_id)}
            >
              <span className={`status-dot ${tone}`} />
              <div>
                <strong>{parcel.source_parcel_id}</strong>
                <span>{formatAddress(parcel) || 'Address not stored'}</span>
                <small>{formatAcres(parcel)} · {parcel.candidate_bucket || parcel.normalized_use || parcel.use_class || 'use n/a'}</small>
              </div>
              <StatusPill tone={tone}>{eligible ? (needsReview ? 'Review' : `${number(parcel.max_units)} units`) : 'No massing'}</StatusPill>
            </button>
          )
        })}
      </div>
    </section>
  )
}

function ParcelHeader({ context, massingAudit }) {
  if (!context) {
    return (
      <section className="hero-card empty-state">
        <p className="eyebrow">Parcel intelligence</p>
        <h2>Select a parcel to load eligibility, massing, financial context, and grounded chat.</h2>
        <p className="muted">Search by folio and county. The first match loads automatically so the map can fly to the stored centroid.</p>
      </section>
    )
  }

  const tone = eligibilityTone(context.entitlement, context.summary)
  const address = formatAddress(context.parcel)
  const confidence = confidenceBand(context.entitlement?.confidence)

  return (
    <section className="hero-card">
      <div className="hero-topline">
        <StatusPill tone={tone}>{eligibilityLabel(context.entitlement, context.summary)}</StatusPill>
        <StatusPill tone="neutral">{confidence}</StatusPill>
        <StatusPill tone={auditTone(massingAudit?.deterministic)}>
          Audit {massingAudit?.deterministic?.sanity_status || context.massing_audit_summary?.sanity_status || 'not loaded'}
        </StatusPill>
      </div>
      <h1>{address || 'Address not stored'}</h1>
      <div className="parcel-meta">
        <span>Folio <strong>{context.parcel?.source_parcel_id}</strong></span>
        <span>{context.parcel?.county || COUNTY_LABELS[context.parcel?.county_fips] || context.parcel?.county_fips}</span>
        <span>{context.jurisdiction?.name || 'Jurisdiction unknown'}</span>
        <span>{formatAcres(context.parcel)}</span>
      </div>
      {context.summary?.eligibility?.review_required && (
        <div className="warning">
          Review required before relying on massing: verify subject zoning and define a developable site boundary.
        </div>
      )}
    </section>
  )
}

function IntelligenceCards({ context }) {
  if (!context) return null
  const cards = [
    {
      label: 'Eligibility',
      value: eligibilityLabel(context.entitlement, context.summary),
      subtext: `${context.entitlement?.confidence || 'unknown'} confidence`,
      tone: eligibilityTone(context.entitlement, context.summary),
    },
    {
      label: 'Massing',
      value: massingLabel(context.entitlement, context.summary),
      subtext: context.summary?.massing?.applies === false
        ? (context.entitlement?.failed_reasons?.[0] || 'ineligible')
        : `${context.entitlement?.max_height_stories || 'n/a'} stories · ${number(context.entitlement?.buildable_sf)} buildable sf`,
      tone: context.summary?.massing?.review_required ? 'warning' : 'neutral',
    },
    {
      label: 'Jurisdiction',
      value: context.jurisdiction?.name || 'Unknown',
      subtext: context.jurisdiction_params?.params_version || 'params missing',
      tone: 'neutral',
    },
    {
      label: 'Market Rent',
      value: money(context.latest_market_rent_source?.market_rent_monthly),
      subtext: context.latest_market_rent_source?.source_type || 'not stored',
      tone: 'neutral',
    },
  ]

  return (
    <section className="cards">
      {cards.map((card) => (
        <article className={`metric-card ${card.tone}`} key={card.label}>
          <span>{card.label}</span>
          <strong>{card.value}</strong>
          <small>{card.subtext}</small>
        </article>
      ))}
    </section>
  )
}

function EvidenceStrip({ context }) {
  if (!context) return null
  const gaps = context.summary?.data_gaps || []
  const failed = context.entitlement?.failed_reasons || []
  const chips = [
    ...gaps.map((gap) => ({ label: gap, tone: 'warning' })),
    ...failed.map((reason) => ({ label: reason, tone: 'danger' })),
  ]

  return (
    <section className="panel evidence-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Source context</p>
          <h2>Known / Estimated / Needs Verification</h2>
        </div>
      </div>
      <div className="verification-grid">
        <div><strong>Known</strong><span>Folio, county, lot size, stored eligibility output</span></div>
        <div><strong>Estimated</strong><span>Massing uses stored assumptions and available zoning context</span></div>
        <div><strong>Needs Verification</strong><span>Zoning counsel should review flags and unmatched/ambiguous inputs</span></div>
      </div>
      {chips.length > 0 && (
        <div className="chips">
          {chips.slice(0, 10).map((chip) => <span className={chip.tone} key={chip.label}>{chip.label}</span>)}
        </div>
      )}
    </section>
  )
}

function AssumptionsPanel({ assumptions, templates, templateName, selectedTemplate, loading, selectedParcelId, onAssumptionChange, onTemplateChange, onRun }) {
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Scenario lab</p>
          <h2>Feasibility assumptions</h2>
        </div>
      </div>
      <label>
        Template
        <select value={templateName} onChange={(event) => onTemplateChange(event.target.value)}>
          {templates.map((template) => (
            <option key={template.template_name} value={template.template_name}>{template.label}</option>
          ))}
        </select>
      </label>
      <p className="muted">{selectedTemplate?.description}</p>
      <div className="form-grid">
        {Object.entries(assumptions).map(([key, value]) => (
          <label key={key}>
            {title(key)}
            <input value={value} onChange={(event) => onAssumptionChange(key, event.target.value)} />
          </label>
        ))}
      </div>
      <button className="primary-action" onClick={onRun} disabled={!selectedParcelId || loading === 'feasibility'}>
        {loading === 'feasibility' ? 'Running...' : 'Run Feasibility'}
      </button>
    </section>
  )
}

function FeasibilityPanel({ feasibility }) {
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Deterministic model</p>
          <h2>Feasibility</h2>
        </div>
        {feasibility?.feasibility?.result && <StatusPill>{title(feasibility.feasibility.result)}</StatusPill>}
      </div>
      {feasibility ? (
        <div className="financial-grid">
          <div><span>Supportable land value</span><strong>{money(feasibility.feasibility?.costs?.supportable_land_value)}</strong></div>
          <div><span>NOI</span><strong>{money(feasibility.feasibility?.income?.noi)}</strong></div>
          <div><span>Market rent</span><strong>{money(feasibility.feasibility?.rents?.market_monthly_rent)}</strong></div>
          <div><span>Warnings</span><strong>{(feasibility.feasibility?.warnings || []).length}</strong></div>
        </div>
      ) : (
        <p className="muted">Run feasibility to see deterministic screening output. Financial logic stays server-side.</p>
      )}
      {feasibility?.feasibility?.warnings?.length > 0 && (
        <div className="chips compact">
          {feasibility.feasibility.warnings.slice(0, 6).map((warning) => <span className="warning" key={warning}>{warning}</span>)}
        </div>
      )}
    </section>
  )
}

function CostAuditPanel({ costAudit, loading, selectedParcelId, onRun }) {
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">AI reviewer</p>
          <h2>Cost Audit</h2>
        </div>
        {costAudit?.status && <StatusPill tone="warning">{title(costAudit.status)}</StatusPill>}
      </div>
      <button className="secondary-action" onClick={onRun} disabled={!selectedParcelId || loading === 'audit'}>
        {loading === 'audit' ? 'Auditing...' : 'Run Cost Audit'}
      </button>
      {costAudit ? (
        <div className="result-card">
          <p>{(costAudit.findings || []).join(' ') || 'No findings returned.'}</p>
          {(costAudit.caveats || []).length > 0 && <small>{costAudit.caveats.join(' ')}</small>}
        </div>
      ) : (
        <p className="muted">Advisory review only. It does not change deterministic feasibility outputs.</p>
      )}
    </section>
  )
}

function MassingAuditPanel({ context, massingAudit, loading, selectedParcelId, onRunAi }) {
  const deterministic = massingAudit?.deterministic
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Zoning sanity</p>
          <h2>Massing Audit</h2>
        </div>
        <StatusPill tone={auditTone(deterministic)}>
          {deterministic?.sanity_status || context?.massing_audit_summary?.sanity_status || 'Not loaded'}
        </StatusPill>
      </div>
      <p className="muted">
        Deterministic rules review stored zoning context and massing output. Optional AI explains ambiguity; it is not the calculator.
      </p>
      {deterministic ? (
        <div className="audit-card">
          <p>{deterministic.summary}</p>
          {deterministic.flags.length > 0 ? (
            <div className="flag-list">
              {deterministic.flags.slice(0, 6).map((flag) => (
                <div className={severityClass(flag.severity)} key={flag.id}>
                  <div>
                    <strong>{flag.title}</strong>
                    <span>{flag.severity} · {flag.category}</span>
                  </div>
                  <p>{flag.explanation}</p>
                  <small>{flag.recommended_action}</small>
                </div>
              ))}
            </div>
          ) : (
            <p className="muted">No deterministic flags returned.</p>
          )}
          <button className="secondary-action" onClick={onRunAi} disabled={!selectedParcelId || loading === 'massing-audit'}>
            {loading === 'massing-audit' ? 'Reviewing...' : 'Run AI Reviewer'}
          </button>
          {massingAudit.ai && (
            <div className="ai-review">
              <StatusPill>AI {massingAudit.ai.status}</StatusPill>
              <p>{massingAudit.ai.summary || 'No AI summary returned.'}</p>
              {(massingAudit.ai.findings || []).slice(0, 5).map((finding, index) => (
                <small key={`${index}-${JSON.stringify(finding)}`}>{typeof finding === 'string' ? finding : JSON.stringify(finding)}</small>
              ))}
            </div>
          )}
        </div>
      ) : (
        <p className="muted">Select a parcel to load deterministic massing sanity checks.</p>
      )}
    </section>
  )
}

function ChatPanel({ chatDraft, chatMessages, context, loading, selectedParcelId, onDraftChange, onPrompt, onSend }) {
  return (
    <section className="panel chat-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Grounded assistant</p>
          <h2>Parcel Chat</h2>
        </div>
        <StatusPill tone="neutral">Server-side AI</StatusPill>
      </div>
      <div className="grounding-note">
        Answers are grounded in the selected parcel context returned by the API. Missing legal, zoning, or financial facts should be treated as diligence items.
      </div>
      <div className="chips prompt-chips">
        {suggestedPrompts.map((prompt) => (
          <button key={prompt} onClick={() => onPrompt(prompt)} disabled={!selectedParcelId || loading === 'chat'}>{prompt}</button>
        ))}
      </div>
      <div className="chat-thread">
        {chatMessages.length === 0 ? (
          <div className="chat-empty">
            <strong>Ask for an analyst summary, diligence gaps, or IC-ready framing.</strong>
            <span>{context ? `Loaded context for ${context.parcel?.source_parcel_id}.` : 'Select a parcel first.'}</span>
          </div>
        ) : (
          chatMessages.map((message) => (
            <div className={`message ${message.role}`} key={message.id}>
              <span>{message.role === 'assistant' ? 'Parcel Assistant' : 'You'}</span>
              <p>{message.content}</p>
              {message.model && <small>Model: {message.model}</small>}
            </div>
          ))
        )}
        {loading === 'chat' && <div className="message assistant loading-bubble">Reading context and drafting answer...</div>}
      </div>
      <div className="chat-composer">
        <textarea value={chatDraft} onChange={(event) => onDraftChange(event.target.value)} rows="4" placeholder="Ask about eligibility, massing, flags, feasibility, or diligence..." />
        <button className="primary-action" onClick={() => onSend()} disabled={!selectedParcelId || !chatDraft || loading === 'chat'}>
          {loading === 'chat' ? 'Asking...' : 'Ask Assistant'}
        </button>
      </div>
      {context && (
        <details className="source-context">
          <summary>Why this answer?</summary>
          <p>
            The API sends parcel context for folio {context.parcel?.source_parcel_id}, jurisdiction {context.jurisdiction?.name || 'unknown'},
            eligibility {eligibilityLabel(context.entitlement, context.summary)}, and deterministic massing {massingLabel(context.entitlement, context.summary)}.
          </p>
        </details>
      )}
    </section>
  )
}

function NotesPanel({ note, status, loading, selectedParcelId, onNoteChange, onStatusChange, onSaveStatus, onSaveNote }) {
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Review queue</p>
          <h2>Notes & Status</h2>
        </div>
      </div>
      <label>
        Status
        <select value={status} onChange={(event) => onStatusChange(event.target.value)}>
          <option value="unreviewed">Unreviewed</option>
          <option value="needs_review">Needs Review</option>
          <option value="watch">Watch</option>
          <option value="pursue">Pursue</option>
          <option value="fail">Fail</option>
        </select>
      </label>
      <button className="secondary-action" onClick={onSaveStatus} disabled={!selectedParcelId || loading === 'status'}>Save Status</button>
      <textarea value={note} onChange={(event) => onNoteChange(event.target.value)} rows="4" placeholder="Add internal note" />
      <button className="primary-action" onClick={onSaveNote} disabled={!selectedParcelId || !note || loading === 'note'}>Add Note</button>
    </section>
  )
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
  const [chatDraft, setChatDraft] = useState(suggestedPrompts[0])
  const [chatMessages, setChatMessages] = useState([])
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

  const tone = eligibilityTone(context?.entitlement, context?.summary)

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
      const nextResults = data.results || []
      setResults(nextResults)
      if (nextResults.length > 0) {
        await loadContext(nextResults[0].parcel_id)
      }
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
      setChatMessages([])
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

  async function sendChat(prompt = chatDraft) {
    if (!selectedParcelId || !prompt) return
    const userMessage = { id: `${Date.now()}-user`, role: 'user', content: prompt }
    setChatMessages((current) => [...current, userMessage])
    setLoading('chat')
    setError('')
    try {
      const data = await api(`/parcels/${selectedParcelId}/chat`, {
        method: 'POST',
        body: JSON.stringify({ message: prompt, scenario: feasibility }),
      })
      setChatMessages((current) => [
        ...current,
        { id: `${Date.now()}-assistant`, role: 'assistant', content: data.message, model: data.model },
      ])
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading('')
    }
  }

  function updateAssumption(key, value) {
    setAssumptions((current) => ({ ...current, [key]: value }))
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
          <h1>Parcel Intelligence Workspace</h1>
          <p className="topbar-subtitle">Map-first diligence, grounded parcel context, deterministic feasibility.</p>
        </div>
        <span className="api-pill">API {API_URL}</span>
      </header>

      {error && <div className="error">{error}</div>}

      <section className="workspace-grid">
        <div className="map-column">
          <ParcelMap context={context} tone={tone} loading={loading} />
          <SearchPanel
            county={county}
            folio={folio}
            loading={loading}
            results={results}
            selectedParcelId={selectedParcelId}
            onCountyChange={setCounty}
            onFolioChange={setFolio}
            onSearch={searchParcels}
            onSelect={loadContext}
          />
        </div>

        <aside className="intelligence-column">
          <ParcelHeader context={context} massingAudit={massingAudit} />
          <IntelligenceCards context={context} />
          <EvidenceStrip context={context} />
          <div className="two-column">
            <AssumptionsPanel
              assumptions={assumptions}
              templates={templates}
              templateName={templateName}
              selectedTemplate={selectedTemplate}
              loading={loading}
              selectedParcelId={selectedParcelId}
              onAssumptionChange={updateAssumption}
              onTemplateChange={setTemplateName}
              onRun={runFeasibility}
            />
            <FeasibilityPanel feasibility={feasibility} />
          </div>
          <div className="two-column">
            <CostAuditPanel costAudit={costAudit} loading={loading} selectedParcelId={selectedParcelId} onRun={runCostAudit} />
            <NotesPanel
              note={note}
              status={status}
              loading={loading}
              selectedParcelId={selectedParcelId}
              onNoteChange={setNote}
              onStatusChange={setStatus}
              onSaveStatus={saveStatus}
              onSaveNote={saveNote}
            />
          </div>
          <MassingAuditPanel
            context={context}
            massingAudit={massingAudit}
            loading={loading}
            selectedParcelId={selectedParcelId}
            onRunAi={runMassingAiAudit}
          />
          <ChatPanel
            chatDraft={chatDraft}
            chatMessages={chatMessages}
            context={context}
            loading={loading}
            selectedParcelId={selectedParcelId}
            onDraftChange={setChatDraft}
            onPrompt={(prompt) => { setChatDraft(prompt); sendChat(prompt) }}
            onSend={sendChat}
          />
        </aside>
      </section>
    </main>
  )
}

export default App
