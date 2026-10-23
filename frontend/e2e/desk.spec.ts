import {expect, test} from '@playwright/test';

test('streams live data, paints the chain and reports latency', async ({
  page,
}) => {
  await page.goto('/');
  await expect(page.getByTestId('status')).toContainText('open');

  // Spot ticks arrive and change over time.
  const spot = page.getByTestId('spot');
  await expect(spot).not.toHaveText('—');
  const first = await spot.textContent();
  await expect
    .poll(async () => spot.textContent(), {timeout: 15_000})
    .not.toBe(first);

  // The latency HUD reports a sane tick-to-pixel number.
  const p50 = page.getByTestId('hud-p50');
  await expect
    .poll(async () => Number(await p50.textContent()), {timeout: 15_000})
    .toBeGreaterThan(0);
  expect(Number(await p50.textContent())).toBeLessThan(500);

  // The canvas is not blank.
  const painted = await page.getByTestId('chain-canvas').evaluate(c => {
    const canvas = c as HTMLCanvasElement;
    const px = canvas
      .getContext('2d')!
      .getImageData(0, 0, canvas.width, canvas.height).data;
    let lit = 0;
    for (let i = 0; i < px.length; i += 4 * 97)
      if (px[i] + px[i + 1] + px[i + 2] > 200) lit++;
    return lit;
  });
  expect(painted).toBeGreaterThan(20);
});

test('click opens a position and risk panel updates; symbols switch', async ({
  page,
}) => {
  await page.goto('/');
  await expect(page.getByTestId('status')).toContainText('open');
  const canvas = page.getByTestId('chain-canvas');
  await expect(page.getByTestId('hud-p50')).not.toHaveText('0.00', {
    timeout: 15_000,
  });
  const box = (await canvas.boundingBox())!;
  // Call price column sits ~39% across; pick a row in the middle of the view.
  await page.mouse.click(box.x + box.width * 0.4, box.y + box.height / 2);
  await expect(page.getByTestId('risk-pnl')).toBeVisible();

  await page.getByRole('button', {name: 'NVDA'}).click();
  await expect(page.getByTestId('spot')).toHaveText(/^1[0-9]{2}\./, {
    timeout: 15_000,
  });
  await expect(page.getByTestId('risk')).toContainText('Click a call or put');
});
