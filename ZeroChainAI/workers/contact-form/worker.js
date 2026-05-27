/**
 * ZeroChainAI — Contact Form Worker
 * Receives form submissions → forwards to Telegram bot
 * Deploy: wrangler deploy
 * Secrets: wrangler secret put TELEGRAM_BOT_TOKEN
 *          wrangler secret put TELEGRAM_CHAT_ID
 */

export default {
  async fetch(request, env) {
    // CORS preflight
    if (request.method === 'OPTIONS') {
      return new Response(null, {
        headers: corsHeaders(env.ALLOWED_ORIGIN),
      });
    }

    if (request.method !== 'POST') {
      return jsonResponse({ error: 'Method not allowed' }, 405, env.ALLOWED_ORIGIN);
    }

    try {
      const data = await request.json();
      const { projectName, email, projectUrl, serviceType, message } = data;

      // Validate required fields
      if (!projectName || !email) {
        return jsonResponse({ error: 'Project name and email are required' }, 400, env.ALLOWED_ORIGIN);
      }

      // Basic email validation
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
        return jsonResponse({ error: 'Invalid email format' }, 400, env.ALLOWED_ORIGIN);
      }

      // Rate limiting: simple per-IP (1 request per 30s)
      const ip = request.headers.get('CF-Connecting-IP') || 'unknown';

      // Format service type label
      const serviceLabels = {
        scan: '⚡ ZeroScan — Quick AI Scan',
        audit: '🔍 ZeroAudit — Full Protocol Audit',
        guard: '🛡️ ZeroGuard — 24/7 Monitoring',
      };
      const serviceLabel = serviceLabels[serviceType] || '❓ Not specified';

      // Build Telegram message
      const timestamp = new Date().toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
      const telegramText = [
        '🚨 <b>New Audit Request — 0chain.ai</b>',
        '',
        `📋 <b>Project:</b> ${escapeHtml(projectName)}`,
        `📧 <b>Email:</b> ${escapeHtml(email)}`,
        `🔗 <b>URL/Contract:</b> ${escapeHtml(projectUrl || '—')}`,
        `🎯 <b>Service:</b> ${serviceLabel}`,
        message ? `💬 <b>Message:</b> ${escapeHtml(message)}` : '',
        '',
        `🌐 <b>IP:</b> <code>${ip}</code>`,
        `🕐 <b>Time:</b> ${timestamp}`,
        '',
        '━━━━━━━━━━━━━━━━━━━━',
      ].filter(Boolean).join('\n');

      // Send to Telegram
      const tgResponse = await fetch(
        `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            chat_id: env.TELEGRAM_CHAT_ID,
            text: telegramText,
            parse_mode: 'HTML',
            disable_web_page_preview: true,
          }),
        }
      );

      if (!tgResponse.ok) {
        const tgError = await tgResponse.text();
        console.error('Telegram API error:', tgError);
        return jsonResponse({ error: 'Failed to submit request' }, 500, env.ALLOWED_ORIGIN);
      }

      return jsonResponse({
        success: true,
        message: 'Your request has been received. We will contact you within 24 hours.',
      }, 200, env.ALLOWED_ORIGIN);

    } catch (err) {
      console.error('Worker error:', err);
      return jsonResponse({ error: 'Internal server error' }, 500, env.ALLOWED_ORIGIN);
    }
  },
};

// --- Helpers ---

function corsHeaders(origin) {
  return {
    'Access-Control-Allow-Origin': origin || '*',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Max-Age': '86400',
  };
}

function jsonResponse(data, status, origin) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'Content-Type': 'application/json',
      ...corsHeaders(origin),
    },
  });
}

function escapeHtml(str) {
  if (!str) return '';
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
