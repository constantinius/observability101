// WORKSHOP 4: replace this file with the browser instrumentation.
export async function otelSpan(name, callback, parent) {
  return callback(parent);
}

export function otelHeaders(context) {
  return {};
}
