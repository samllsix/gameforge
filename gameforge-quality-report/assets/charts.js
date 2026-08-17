(function() {
  var style = getComputedStyle(document.documentElement);
  var accent = style.getPropertyValue('--accent').trim();
  var accent2 = style.getPropertyValue('--accent2').trim();
  var ink = style.getPropertyValue('--ink').trim();
  var muted = style.getPropertyValue('--muted').trim();
  var rule = style.getPropertyValue('--rule').trim();
  var bg2 = style.getPropertyValue('--bg2').trim();
  var warn = style.getPropertyValue('--warn').trim();
  var danger = style.getPropertyValue('--danger').trim();

  // --- Chart 1: Script Quality Scores ---
  var chart1 = echarts.init(document.getElementById('chart-script-quality'), null, { renderer: 'svg' });
  chart1.setOption({
    animation: false,
    tooltip: { trigger: 'axis', appendToBody: true },
    grid: { left: '15%', right: '8%', top: '8%', bottom: '15%' },
    xAxis: {
      type: 'category',
      data: ['player.gd', 'enemy.gd', 'coin.gd', 'pickup.gd', 'collectible.gd', 'score_manager.gd', 'battle_manager.gd', 'hud.gd', 'coin_pickup.gd', 'score_pickup.gd', 'game_manager.gd', 'ai_controller.gd'],
      axisLabel: {
        color: muted,
        rotate: 45,
        fontSize: 10,
        interval: 0
      },
      axisLine: { lineStyle: { color: rule } }
    },
    yAxis: {
      type: 'value',
      max: 10,
      min: 0,
      axisLabel: { color: muted, fontSize: 11 },
      axisLine: { lineStyle: { color: rule } },
      splitLine: { lineStyle: { color: rule, type: 'dashed' } }
    },
    series: [{
      type: 'bar',
      data: [
        { value: 9, itemStyle: { color: accent2 } },
        { value: 8, itemStyle: { color: accent2 } },
        { value: 8, itemStyle: { color: accent2 } },
        { value: 8, itemStyle: { color: accent2 } },
        { value: 5, itemStyle: { color: warn } },
        { value: 8, itemStyle: { color: accent2 } },
        { value: 4, itemStyle: { color: danger } },
        { value: 7, itemStyle: { color: accent2 } },
        { value: 5, itemStyle: { color: warn } },
        { value: 5, itemStyle: { color: warn } },
        { value: 9, itemStyle: { color: accent2 } },
        { value: 8, itemStyle: { color: accent2 } }
      ],
      barWidth: '50%',
      label: {
        show: true,
        position: 'top',
        color: ink,
        fontSize: 11,
        formatter: '{c}'
      }
    }]
  });
  window.addEventListener('resize', function() { chart1.resize(); });

  // --- Chart 2: Scene Quality Radar ---
  var chart2 = echarts.init(document.getElementById('chart-scene-radar'), null, { renderer: 'svg' });
  chart2.setOption({
    animation: false,
    tooltip: { trigger: 'item', appendToBody: true },
    radar: {
      indicator: [
        { name: '节点类型正确性', max: 10 },
        { name: '碰撞体配置', max: 10 },
        { name: '脚本绑定', max: 10 },
        { name: '视觉材质', max: 10 },
        { name: '平台布局', max: 10 },
        { name: '相机配置', max: 10 },
        { name: '边界设置', max: 10 },
        { name: 'SubResource复用', max: 10 }
      ],
      axisName: { color: ink, fontSize: 11 },
      splitLine: { lineStyle: { color: rule } },
      splitArea: { areaStyle: { color: ['rgba(255,255,255,0.02)', 'rgba(255,255,255,0.04)'] } },
      axisLine: { lineStyle: { color: rule } }
    },
    series: [{
      type: 'radar',
      data: [{
        value: [10, 8, 9, 8, 3, 4, 5, 7],
        name: '场景质量',
        areaStyle: { color: accent + '33' },
        lineStyle: { color: accent, width: 2 },
        itemStyle: { color: accent }
      }]
    }]
  });
  window.addEventListener('resize', function() { chart2.resize(); });
})();
