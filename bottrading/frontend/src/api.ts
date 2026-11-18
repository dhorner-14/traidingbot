export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

// La API key se pasa en cada llamada desde el estado de React (memoria). No se guarda en el navegador.
export async function api<T>(path: string, key: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: { "Content-Type": "application/json", "X-API-Key": key, ...(init.headers ?? {}) },
    });
  } catch {
    throw new ApiError(0, "No se pudo conectar con el backend. Verifica que esté corriendo en el puerto 8000.");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* respuesta sin JSON: se usa el texto HTTP */
    }
    throw new ApiError(res.status, res.status === 401 ? "API key incorrecta o ausente." : detail);
  }
  return res.json() as Promise<T>;
}
