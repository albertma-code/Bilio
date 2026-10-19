async (page) => {
  const results = [], errors = [];
  page.on('pageerror', error => errors.push(error.message));
  for (const theme of ['coral', 'graphite']) {
    await page.setViewportSize({width:1000,height:720});
    await page.goto(`http://127.0.0.1:1421/tests/ui/preview.html?theme=${theme}`);
    await page.getByRole('button',{name:'B站 · 大会员',exact:true}).waitFor();
    await page.locator('.task-row').nth(3).waitFor();
    const contrast = await page.evaluate(() => {
      const styles=getComputedStyle(document.documentElement);
      const color=(key)=>styles.getPropertyValue(key).trim();
      const luminance=hex=>{
        const rgb=hex.replace('#','').match(/../g).map(v=>parseInt(v,16)/255).map(v=>v<=0.04045?v/12.92:((v+0.055)/1.055)**2.4);
        return rgb[0]*0.2126+rgb[1]*0.7152+rgb[2]*0.0722;
      };
      const ratio=(fg,bg)=>{const a=luminance(fg),b=luminance(bg);return (Math.max(a,b)+0.05)/(Math.min(a,b)+0.05);};
      return [
        ['body',color('--text'),color('--page')],['secondary',color('--secondary'),color('--page')],
        ['muted',color('--muted'),color('--sidebar')],['primary','#ffffff',color('--accent')],
        ['selected',color('--selected-text'),color('--selected-bg')],['success',color('--success'),color('--page')],
        ['error',color('--danger'),color('--danger-bg')],['warning',color('--warning'),color('--warning-bg')],
      ].map(([name,fg,bg])=>({name,ratio:ratio(fg,bg)}));
    });
    if(contrast.some(c=>c.ratio<4.5)) throw new Error(`${theme}: low contrast ${JSON.stringify(contrast)}`);
    for(const width of [1000,640]) {
      await page.setViewportSize({width,height:width===640?480:720});
      if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)) throw new Error('horizontal overflow');
      await page.screenshot({path:`/tmp/bilio-design-${theme}-library-${width}.png`,fullPage:true});
    }
    await page.setViewportSize({width:1000,height:720});
    await page.getByRole('button',{name:'新建下载',exact:true}).first().click();
    await page.getByPlaceholder('粘贴 B站视频、番剧或合集链接').fill('https://www.bilibili.com/video/BVpreview');
    await page.getByRole('button',{name:'解析',exact:true}).click();
    await page.getByRole('button',{name:'加入下载列表',exact:true}).waitFor();
    await page.locator('.n-drawer:visible').last().evaluate(el=>Promise.all(el.getAnimations({subtree:true}).map(a=>a.finished.catch(()=>{}))));
    for(const width of [1000,640]) {
      await page.setViewportSize({width,height:width===640?480:720});
      const box=await page.locator('.submit-bar').boundingBox();
      if(box.y<0||box.y+box.height>(width===640?481:721)) throw new Error('submit outside viewport');
      await page.screenshot({path:`/tmp/bilio-design-${theme}-parser-${width}.png`,fullPage:true});
    }
    await page.keyboard.press('Escape');
    await page.setViewportSize({width:1000,height:720});
    await page.getByRole('button',{name:'设置',exact:true}).click();
    await page.getByRole('heading',{name:'B站账号',exact:true}).waitFor();
    await page.locator('.n-drawer:visible').last().evaluate(el=>Promise.all(el.getAnimations({subtree:true}).map(a=>a.finished.catch(()=>{}))));
    await page.screenshot({path:`/tmp/bilio-design-${theme}-account.png`,fullPage:true});
    results.push({theme,contrast});
  }
  if(errors.length) throw new Error(errors.join('\n'));
  return {results,errors,fixture:'synthetic tasks only; no native calls or credentials'};
}
