import { Route, Routes } from 'react-router-dom'
import { UploadPage } from './pages/UploadPage'
import { JobPage } from './pages/JobPage'

function App() {
  return (
    <Routes>
      <Route path="/" element={<UploadPage />} />
      <Route path="/jobs/:jobId" element={<JobPage />} />
    </Routes>
  )
}

export default App
