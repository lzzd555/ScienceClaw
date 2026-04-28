<template>
  <RouterView />
</template>

<style>
:root {
  color: #1f2937;
  background: #eef2f7;
  font-family:
    Inter, "PingFang SC", "Microsoft YaHei", Arial, sans-serif;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  min-width: 1160px;
  background: #eef2f7;
}

.page {
  padding: 16px;
}

.toolbar {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: end;
}

.panel {
  background: #fff;
  border: 1px solid #d9e0ea;
  border-radius: 6px;
  padding: 14px;
}

.page-title {
  margin: 0 0 12px;
  font-size: 18px;
  font-weight: 650;
}

.muted {
  color: #6b7280;
}
</style>
