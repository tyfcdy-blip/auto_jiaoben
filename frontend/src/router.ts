import { createRouter, createWebHashHistory } from 'vue-router'
import OverviewView from './views/OverviewView.vue'
import ProgramsView from './views/ProgramsView.vue'
import TasksView from './views/TasksView.vue'
import RunsView from './views/RunsView.vue'
import RunDetailView from './views/RunDetailView.vue'
import NodesView from './views/NodesView.vue'
import NodeDetailView from './views/NodeDetailView.vue'
import BatchesView from './views/BatchesView.vue'
import CallersView from './views/CallersView.vue'
import SchedulesView from './views/SchedulesView.vue'
import AlertsView from './views/AlertsView.vue'
import NotificationSettingsView from './views/NotificationSettingsView.vue'
import NotificationRecordsView from './views/NotificationRecordsView.vue'
import PeopleView from './views/PeopleView.vue'
import AuditView from './views/AuditView.vue'
export const router = createRouter({
  history: createWebHashHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', component: OverviewView },
    { path: '/programs', component: ProgramsView },
    { path: '/tasks', component: TasksView },
    { path: '/runs', component: RunsView },
    { path: '/runs/:id', component: RunDetailView },
    { path: '/nodes', component: NodesView },
    { path: '/nodes/:id', component: NodeDetailView },
    { path: '/batches', component: BatchesView },
    { path: '/callers', component: CallersView },
    { path: '/schedules', component: SchedulesView },
    { path: '/alerts', component: AlertsView },
    {
      path: '/notification-channels',
      component: NotificationSettingsView,
      props: { section: 'channels' },
    },
    {
      path: '/notification-rules',
      component: NotificationSettingsView,
      props: { section: 'rules' },
    },
    {
      path: '/notification-records',
      component: NotificationRecordsView,
    },
    { path: '/people', component: PeopleView },
    { path: '/audit', component: AuditView },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
