export const stubsViewTemplate = `
<section class="panel">
  <h2>增补 / 拆笼分拨</h2>
  <div class="stub-banner">
    拆笼已按《拆笼分拨纪要 2026-11-22》锁定为<b>整笔切分同事务落库</b>：
    同一提交内扣减原笼可用、新建左/右两只子笼并写审计，任一步失败整笔回滚。
    影子子笼路径未采用，提交即记“拆笼偏航”并拒写。增补入笼仍为空桩。
  </div>
  <div class="grid-2">
    <div>
      <h3>增补入笼</h3>
      <div class="form-row">
        <div>
          <label>笼</label>
          <select v-model.number="topupForm.cage_id">
            <option :value="0">请选择</option>
            <option v-for="c in cages" :key="'t'+c.id" :value="c.id">{{ c.cage_code }}</option>
          </select>
        </div>
        <div>
          <label>金额</label>
          <input type="number" v-model.number="topupForm.amount" min="1"/>
        </div>
        <div>
          <label>模式</label>
          <select v-model="topupForm.mode">
            <option>同笼扩代</option>
            <option>新笼桥接</option>
          </select>
        </div>
      </div>
      <div class="actions">
        <button @click="doTopupStub">登记增补空桩</button>
      </div>
      <table>
        <thead><tr><th>ID</th><th>笼</th><th>金额</th><th>模式</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="j in topupJobs" :key="j.id">
            <td>{{ j.id }}</td>
            <td>{{ j.cage_code }}</td>
            <td>{{ fmt(j.amount) }}</td>
            <td>{{ j.mode }}</td>
            <td>{{ j.status }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div>
      <h3>拆笼分拨</h3>
      <p class="hint">左右金额之和须等于所选笼当前可用额。拆分后原笼可用归零、状态为“已拆分”，两只子份额可各自核验与处置。</p>
      <div class="form-row">
        <div>
          <label>笼</label>
          <select v-model.number="splitForm.cage_id" @change="syncSplitAmounts">
            <option :value="0">请选择</option>
            <option v-for="c in splittableCages" :key="'s'+c.id" :value="c.id">
              {{ c.cage_code }} · 可用 {{ fmt(c.available) }} · {{ c.status }}
            </option>
          </select>
        </div>
        <div>
          <label>左金额</label>
          <input type="number" v-model.number="splitForm.left_amount" min="1"/>
        </div>
        <div>
          <label>右金额</label>
          <input type="number" v-model.number="splitForm.right_amount" min="1"/>
        </div>
        <div>
          <label>锁定模式</label>
          <input :value="splitForm.mode" disabled/>
        </div>
      </div>
      <div class="actions">
        <button class="ok" @click="doSplit">整笔切分</button>
        <button class="secondary" @click="refresh">刷新</button>
      </div>
      <div v-if="splitResult" class="ok-msg">
        已切分作业 #{{ splitResult.job_id }}：{{ splitResult.left_cage.cage_code }}
        {{ fmt(splitResult.left_cage.amount) }} ／ {{ splitResult.right_cage.cage_code }}
        {{ fmt(splitResult.right_cage.amount) }}
      </div>
      <h4>拆分作业</h4>
      <table>
        <thead><tr><th>ID</th><th>原笼</th><th>左份额</th><th>右份额</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="j in splitJobs" :key="j.id">
            <td>{{ j.id }}</td>
            <td>{{ j.cage_code }}</td>
            <td>{{ j.left_cage_code }} · {{ fmt(j.left_amount) }}</td>
            <td>{{ j.right_cage_code }} · {{ fmt(j.right_amount) }}</td>
            <td><span class="badge" :class="j.status==='已切分'?'pass':'fail'">{{ j.status }}</span></td>
          </tr>
        </tbody>
      </table>
      <h4>子份额（拆分关系）</h4>
      <table>
        <thead><tr><th>原笼</th><th>侧</th><th>子笼</th><th>金额</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="l in splitLinks" :key="l.id">
            <td>{{ l.parent_cage_code }}</td>
            <td>{{ l.side }}</td>
            <td>{{ l.child_cage_code }}</td>
            <td>{{ fmt(l.amount) }}</td>
            <td>{{ l.child_status }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</section>
`;
