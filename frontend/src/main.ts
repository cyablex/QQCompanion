import { createApp } from 'vue'
import App from './App.vue'
import './theme.css'

createApp(App).mount('#app')
window.dispatchEvent(new Event('dafeiyu:mounted'))
