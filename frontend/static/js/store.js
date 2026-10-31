// Global application state + tiny event emitter. SSE events are re-emitted here.
const listeners = {};

export const store = {
  status: null,       // /api/status payload
  device: {},         // latest device status
  mode: "simulation",
  settings: null,
  latest: null,       // latest measurement sample
  stats: null,        // window statistics
  profiles: null,
  simProfiles: null,
  references: null,
  manufacturers: null,
  guided: (() => { try { return localStorage.getItem("icv.guided") !== "0"; } catch (e) { return true; } })(),

  on(evt, fn) { (listeners[evt] = listeners[evt] || []).push(fn); return () => this.off(evt, fn); },
  off(evt, fn) { listeners[evt] = (listeners[evt] || []).filter((f) => f !== fn); },
  emit(evt, data) { (listeners[evt] || []).slice().forEach((fn) => { try { fn(data); } catch (e) { console.error(e); } }); },

  isSim() { return this.mode === "simulation"; },
  isStreaming() {
    const s = this.device.state;
    return this.mode === "simulation" ? (s === "simulating") : s === "streaming";
  },
};

// Cached reference data with explicit refresh.
import { api } from "./api.js";
export async function loadProfiles(force = false) {
  if (!store.profiles || force) store.profiles = await api.get("/api/profiles");
  return store.profiles;
}
export async function loadSimProfiles(force = false) {
  if (!store.simProfiles || force) store.simProfiles = await api.get("/api/sim/profiles");
  return store.simProfiles;
}
export async function loadReferences(force = false) {
  if (!store.references || force) store.references = await api.get("/api/references");
  return store.references;
}
export async function loadManufacturers(force = false) {
  if (!store.manufacturers || force) store.manufacturers = await api.get("/api/manufacturers");
  return store.manufacturers;
}
