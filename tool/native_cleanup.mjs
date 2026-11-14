// Cleanup uses owned process handles, never a reusable emulator console port.
export const childAlive = proc => proc && Number.isInteger(proc.pid) && proc.exitCode === null && proc.signalCode === null;

export async function stopOwned(proc) {
  if (!childAlive(proc)) return;
  const exited = new Promise(resolve => proc.once('exit', resolve));
  proc.kill();
  let timer;
  try {
    await Promise.race([exited, new Promise((_, reject) => {
      timer = setTimeout(() => {if (childAlive(proc)) reject(new Error('Owned process did not stop')); else resolve();}, 5000);
    })]);
  } finally {clearTimeout(timer);}
}

export async function cleanupOwned(result, closers) {
  const failures = [];
  for (const [name, close] of closers) {
    let timer;
    try {
      await Promise.race([Promise.resolve().then(close), new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error('Cleanup exceeded five seconds')), 5000);
      })]);
    } catch (error) {failures.push(`${name}: ${error.message}`);}
    finally {clearTimeout(timer);}
  }
  if (!failures.length) return result;
  return {...result, blocked: {prerequisite: 'native', reason:
    [result?.blocked?.reason, `Native cleanup failed: ${failures.join('; ')}`].filter(Boolean).join('; ')}};
}
