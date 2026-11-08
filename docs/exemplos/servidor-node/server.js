// Exemplo de servidor para o CDBS.OS 4.0 — Node.js puro (sem npm install).
// No Termux, instale o Node uma vez: pkg install nodejs -y
// Painel → Servidores → Criar: tipo Node.js, arquivo de entrada server.js, caminho de saúde /saude.
// Compacte esta pasta em .zip, envie e toque em "Implantar". Fica em https://SEU-LINK/NOME/.
'use strict';
const http = require('http');
const fs = require('fs');
const path = require('path');

const PORT = Number(process.env.PORT || 8102);
const HOST = process.env.HOST || '127.0.0.1';                 // só no celular; a internet chega pelo CDBS
const DATA = process.env.CDBS_APP_DATA || path.join(__dirname, 'dados');   // pasta que sobrevive a implantações
fs.mkdirSync(DATA, { recursive: true });
const FILE = path.join(DATA, 'contador.json');

function ler() {
  try { return JSON.parse(fs.readFileSync(FILE, 'utf8')); } catch { return { visitas: 0 }; }
}
function gravar(dados) {
  fs.writeFileSync(FILE + '.tmp', JSON.stringify(dados));
  fs.renameSync(FILE + '.tmp', FILE);                          // troca atômica
}

const server = http.createServer((req, res) => {
  console.log(`${req.method} ${req.url}`);                     // aparece no Registro do painel
  if (req.url === '/saude') { res.writeHead(200, { 'Content-Type': 'text/plain' }); return res.end('ok'); }
  if (req.url === '/api/visitas') {
    const dados = ler();
    dados.visitas += 1;
    gravar(dados);
    res.writeHead(200, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify(dados));
  }
  res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
  res.end(`<!doctype html><meta charset=utf-8><title>Contador</title>
<body style="font:16px system-ui;max-width:520px;margin:40px auto;padding:0 16px">
<h1>Contador de visitas</h1><p id=n>…</p>
<script>fetch('api/visitas').then(r=>r.json()).then(d=>{document.getElementById('n').textContent=d.visitas+' visitas'})</script>`);
});

server.listen(PORT, HOST, () => console.log(`contador em ${HOST}:${PORT}`));
process.on('SIGTERM', () => server.close(() => process.exit(0)));   // o CDBS pede para parar com SIGTERM
