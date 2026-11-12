// Explicit span contexts keep this demo small: no global async patching needed.
import {ROOT_CONTEXT, SpanStatusCode, propagation, trace} from '@opentelemetry/api';
import {resourceFromAttributes} from '@opentelemetry/resources';
import {BatchSpanProcessor} from '@opentelemetry/sdk-trace-base';
import {WebTracerProvider} from '@opentelemetry/sdk-trace-web';
import {OTLPTraceExporter} from '@opentelemetry/exporter-trace-otlp-http';

const provider = new WebTracerProvider({
  resource: resourceFromAttributes({'service.name': 'todo-frontend'}),
  spanProcessors: [new BatchSpanProcessor(new OTLPTraceExporter({
    // Vite proxies this same-origin URL to the collector; no collector CORS needed.
    url: new URL('/otlp/v1/traces', window.location.origin).href,
  }), {scheduledDelayMillis: 1000})],
});
provider.register();

export async function otelSpan(name, callback, parent = ROOT_CONTEXT) {
  const span = trace.getTracer('todo-frontend').startSpan(name, {}, parent);
  try {
    return await callback(trace.setSpan(parent, span));
  } catch (error) {
    span.recordException(error);
    span.setStatus({code: SpanStatusCode.ERROR, message: error.message});
    throw error;
  } finally {
    span.end();
  }
}

export function otelHeaders(context) {
  const headers = {};
  propagation.inject(context, headers);
  return headers;
}
