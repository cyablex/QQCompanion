<script setup lang="ts">
defineProps<{ name: string; size?: number }>()
const paths: Record<string, string> = {
  pen: 'm14 4 6 6M4 20l5-1L21 7l-4-4L5 15Zm0 0 5-1',
  bookmark: 'M6 3h12v18l-6-4-6 4Z',
  search: 'M16 16l5 5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
  sparkle: 'm12 2 3 7 7 3-7 3-3 7-3-7-7-3 7-3Z',
  pet: 'M7 4 3 3v7a9 9 0 0 0 18 0V3l-4 1M8 11h.01M16 11h.01M10 15h4',
  calendar: 'M4 5h16v16H4ZM8 3v4m8-4v4M4 10h16M8 14h2m4 0h2M8 18h2',
  minimize: 'M5 12h14', maximize: 'M5 5h14v14H5Z', restore: 'M8 8h11v11H8ZM5 15H4V4h11v1',
  home: 'm3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z',
  settings: 'M4 7h9m4 0h3M4 17h3m4 0h9M13 4v6M7 14v6',
  connection: 'm9 15 6-6M8 16l-1 1a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m2 10a4 4 0 0 0 6 0l4-4a4 4 0 0 0-6-6l-1 1',
  play: 'm8 5 11 7-11 7Z', pause: 'M8 5v14M16 5v14', stop: 'M6 6h12v12H6Z',
  refresh: 'M20 7v5h-5M4 17v-5h5M6 6a8 8 0 0 1 13 2M18 18a8 8 0 0 1-13-2',
  game: 'M7 8h10c3 0 5 9 3 11-2 1-4-3-5-3H9c-1 0-3 4-5 3-2-2 0-11 3-11ZM7 10v5m-2-2h4m7-1h.01m3 2h.01',
  arrow: 'M5 12h14m-5-5 5 5-5 5', check: 'm5 12 4 4L19 6',
  clock: 'M12 8v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
  shield: 'm12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Zm-4 9 3 3 5-6',
  close: 'm6 6 12 12M6 18 18 6',
  music: 'M9 18V5l11-2v13M9 8l11-2M9 18c0 2-6 3-6 0s6-2 6 0Zm11-2c0 2-6 3-6 0s6-2 6 0Z',
  gallery: 'M4 4h16v16H4ZM4 15l5-5 4 4 3-3 4 4M15 8h.01',
}
</script>
<template><svg :width="size || 20" :height="size || 20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="paths[name] || paths.home" /></svg></template>
