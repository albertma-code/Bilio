// Run after verify.js installs its isolated IPC fixture into this page.
async (page) => {
  await page.goto('http://127.0.0.1:1421/');
  await page.getByText('引擎就绪',{exact:true}).waitFor();
  const render=await page.evaluate(async()=>{
    const test=window.__bilioTest,start=performance.now();
    for(let index=0;index<1000;index++) {
      const task={jobId:300000+index,sequence:1,status:'completed',progress:100,downloadedBytes:1000,totalBytes:1000,
        config:{url:'https://www.bilibili.com/video/BVfixture',title:'性能样本 record-'+index,policy:{mode:'best'},formatLabel:'1080P',output_dir:'/tmp/bilio-fixture'}};
      test.state.tasks.push(task);test.emit({id:task.jobId,type:'task',data:task});
    }
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    return performance.now()-start;
  });
  const start=Date.now();
  await page.getByPlaceholder('搜索任务名称').fill('record-999');
  await page.locator('.task-row').first().waitFor();
  return {fixture:'1000 synthetic records; browser only',render_ms:Math.round(render),search_ms:Date.now()-start,matching_rows:await page.locator('.task-row').count()};
}
