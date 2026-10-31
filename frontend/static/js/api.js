// Thin fetch wrapper: resolves to `data`, throws Error(message) on API errors.
async function call(method, path, body, isForm = false) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    if (isForm) opts.body = body;
    else { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  }
  let res;
  try { res = await fetch(path, opts); }
  catch (e) { throw new Error("IC VISION server is not reachable. Is run.py still running?"); }
  let json = null;
  try { json = await res.json(); } catch (e) { /* non-JSON */ }
  if (!res.ok || !json || json.ok === false) {
    throw new Error((json && json.error) || `${method} ${path} failed (${res.status})`);
  }
  return json;
}

export const api = {
  get: (p) => call("GET", p).then((j) => j.data),
  getFull: (p) => call("GET", p),
  post: (p, b = {}) => call("POST", p, b).then((j) => j.data),
  postFull: (p, b = {}) => call("POST", p, b),
  put: (p, b = {}) => call("PUT", p, b).then((j) => j.data),
  del: (p) => call("DELETE", p).then((j) => j.data),
  upload: (p, form) => call("POST", p, form, true).then((j) => j.data),
};
