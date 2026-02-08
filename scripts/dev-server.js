import express from 'express';
import path from 'path';

const app = express();
const PORT = 3000;

// Serve static files
app.use(express.static('.'));

// Fallback to index.html for all routes (SPA behavior)
app.get(/.*/, (req, res) => {
  if (!res.headersSent && req.path !== '/favicon.ico') {
    res.sendFile(path.join(process.cwd(), 'index.html'));
  }
});

app.listen(PORT, () => {
  console.log(`\n🚀 WG - Waveguide Generator running at http://localhost:${PORT}`);
  console.log(`\nAvailable endpoints:`);
  console.log(`  - http://localhost:${PORT}/`);
});
