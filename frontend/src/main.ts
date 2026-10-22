import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import './style.css'
import App from './App.vue'
import Login from './views/Login.vue'
import Workbench from './views/Workbench.vue'
import Inputs from './views/Inputs.vue'
import PlanPreview from './views/PlanPreview.vue'
import ScheduleWorkspace from './views/ScheduleWorkspace.vue'
import PlanningJobs from './views/PlanningJobs.vue'
import ExecutionReset from './views/ExecutionReset.vue'
import DemandManagement from './views/DemandManagement.vue'
import InitializationSetup from './views/InitializationSetup.vue'
import Versions from './views/Versions.vue'
import SystemManagement from './views/SystemManagement.vue'
import { basePath, principal } from './api'
import { useSharedPlan } from './useSharedPlan'
const router = createRouter({ history: createWebHistory(`${basePath}/`), routes: [
  { path: '/login', component: Login },
  { path: '/', component: Workbench },
  { path: '/schedule', component: ScheduleWorkspace, meta: { section: 'schedule' } },
  { path: '/schedule/jobs', component: PlanningJobs, meta: { section: 'schedule' } },
  { path: '/schedule/reset', component: ExecutionReset, meta: { section: 'schedule' } },
  { path: '/schedule/day', component: PlanPreview, meta: { section: 'schedule' } },
  { path: '/data/demand', component: DemandManagement, meta: { section: 'data' } },
  { path: '/data/base', component: Inputs, meta: { section: 'data' } },
  { path: '/data/initialization', component: InitializationSetup, meta: { section: 'data' } },
  { path: '/versions', component: Versions },
  { path: '/system', component: SystemManagement },
  { path: '/initialize', redirect: '/data/demand' },
  { path: '/admin', redirect: '/system' },
  { path: '/inputs', redirect: '/data/base' },
  { path: '/plans/shared', redirect: '/schedule' },
  { path: '/plans/preview', redirect: '/schedule/day' },
  { path: '/:pathMatch(.*)*', redirect: '/' },
] })
const sharedPlan=useSharedPlan()
router.beforeEach(async to=>{if(principal.value&&to.path!=='/login')await sharedPlan.refresh()})
createApp(App).use(router).use(ElementPlus, { locale: zhCn }).mount('#app')
