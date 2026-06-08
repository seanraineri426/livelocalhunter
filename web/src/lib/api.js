export const API_URL = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'

export async function api(path, options = {}) {
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
