import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import './style.css'
import App from './App.vue'
import Login from './views/Login.vue'
import Home from './views/Home.vue'
import Authorizations from './views/Authorizations.vue'
import Inputs from './views/Inputs.vue'
import PlanPreview from './views/PlanPreview.vue'
import SharedPlans from './views/SharedPlans.vue'
import Initialization from './views/Initialization.vue'
import { basePath } from './api'
const router = createRouter({ history: createWebHistory(`${basePath}/`), routes: [
  { path: '/login', component: Login }, { path: '/', component: Home },
  { path: '/initialize', component: Initialization }, { path: '/admin', component: Authorizations }, { path: '/inputs', component: Inputs }, { path: '/plans/preview', component: PlanPreview }, { path: '/plans/shared', component: SharedPlans }, { path: '/:pathMatch(.*)*', redirect: '/' },
] })
createApp(App).use(router).use(ElementPlus, { locale: zhCn }).mount('#app')
